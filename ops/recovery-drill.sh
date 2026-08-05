#!/usr/bin/env bash
# ops/recovery-drill.sh — 备份恢复演练
# 用法: ./ops/recovery-drill.sh --backup <path.tar.gz.enc> --passphrase-file <path> [--output-dir <dir>]
#
# 验证加密备份的完整性：解密 → 解压 → 核对 manifest → 报告。

set -euo pipefail

BACKUP=""
PASSPHRASE_FILE=""
OUTPUT_DIR=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --backup) BACKUP="$2"; shift 2 ;;
        --passphrase-file) PASSPHRASE_FILE="$2"; shift 2 ;;
        --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

if [[ -z "$BACKUP" ]] || [[ -z "$PASSPHRASE_FILE" ]]; then
    echo "ERROR: --backup and --passphrase-file required" >&2
    exit 1
fi

if [[ ! -f "$BACKUP" ]]; then
    echo "ERROR: backup file not found: $BACKUP" >&2
    exit 1
fi

if [[ ! -f "$PASSPHRASE_FILE" ]]; then
    echo "ERROR: passphrase file not found: $PASSPHRASE_FILE" >&2
    exit 1
fi

PASSPHRASE=$(cat "$PASSPHRASE_FILE")
if [[ -z "$PASSPHRASE" ]]; then
    echo "ERROR: passphrase file is empty" >&2
    exit 1
fi

HASH_FILE="${BACKUP}.sha256"
if [[ ! -f "$HASH_FILE" ]]; then
    echo "WARNING: detached hash file missing: $HASH_FILE" >&2
fi

# ─── 1. 验证 detached hash ───────────────────────────────────────────────────
if [[ -f "$HASH_FILE" ]]; then
    echo "[*] Verifying detached hash..."
    if ! sha256sum -c "$HASH_FILE"; then
        echo "ERROR: detached hash verification failed" >&2
        exit 1
    fi
    echo "  ✓ detached hash OK"
fi

# ─── 2. 解密 ─────────────────────────────────────────────────────────────────
WORK_DIR=$(mktemp -d)
trap 'rm -rf "$WORK_DIR"' EXIT

TAR_GZ="$WORK_DIR/recovered.tar.gz"
echo "[*] Decrypting backup..."
openssl enc -aes-256-cbc -pbkdf2 -iter 100000 -d -salt \
    -in "$BACKUP" -out "$TAR_GZ" -pass "pass:$PASSPHRASE"
echo "  ✓ decrypted"

# ─── 3. 解压 ─────────────────────────────────────────────────────────────────
EXTRACT_DIR="${OUTPUT_DIR:-$WORK_DIR/extracted}"
mkdir -p "$EXTRACT_DIR"
echo "[*] Extracting archive..."
tar -xzf "$TAR_GZ" -C "$EXTRACT_DIR"
echo "  ✓ extracted to $EXTRACT_DIR"

# ─── 4. 核对 manifest ────────────────────────────────────────────────────────
MANIFEST="$EXTRACT_DIR/backup-manifest.json"
if [[ ! -f "$MANIFEST" ]]; then
    echo "ERROR: manifest not found in extracted backup" >&2
    exit 1
fi

echo "[*] Verifying manifest..."
MISMATCH=0
TOTAL=0

# 使用 jq 读取 manifest（如可用），否则回退到 Python
if command -v jq &>/dev/null; then
    while IFS= read -r line; do
        file=$(echo "$line" | jq -r '.file')
        expected=$(echo "$line" | jq -r '.hash')
        actual=$(sha256sum "$EXTRACT_DIR/$file" | awk '{print $1}')
        TOTAL=$((TOTAL + 1))
        if [[ "$actual" != "$expected" ]]; then
            echo "  ✗ hash mismatch: $file" >&2
            MISMATCH=$((MISMATCH + 1))
        fi
    done < <(jq -r '.files | to_entries | map({file: .key, hash: .value}) | .[] | @json' "$MANIFEST")
else
    echo "  (jq not available, using Python for manifest parsing)"
    python3 - "$EXTRACT_DIR" "$MANIFEST" <<'PY'
import json, hashlib, sys
extract_dir, manifest_path = sys.argv[1:3]
with open(manifest_path) as f:
    manifest = json.load(f)
mismatch = 0
total = 0
for rel, expected in manifest.get("files", {}).items():
    total += 1
    path = f"{extract_dir}/{rel}"
    with open(path, "rb") as f:
        actual = hashlib.sha256(f.read()).hexdigest()
    if actual != expected:
        print(f"  ✗ hash mismatch: {rel}", file=sys.stderr)
        mismatch += 1
print(f"{{\"total\":{total},\"mismatch\":{mismatch}}}")
PY
fi

# ─── 5. 报告 ─────────────────────────────────────────────────────────────────
if [[ "$MISMATCH" -eq 0 ]]; then
    echo "  ✓ manifest verification passed ($TOTAL files)"
    echo "{\"phase\":\"recovery\",\"backup\":\"$BACKUP\",\"extracted_to\":\"$EXTRACT_DIR\",\"files_checked\":$TOTAL,\"mismatches\":$MISMATCH,\"status\":\"PASS\"}"
    exit 0
else
    echo "ERROR: $MISMATCH of $TOTAL files failed hash verification" >&2
    echo "{\"phase\":\"recovery\",\"backup\":\"$BACKUP\",\"files_checked\":$TOTAL,\"mismatches\":$MISMATCH,\"status\":\"FAIL\"}"
    exit 1
fi
