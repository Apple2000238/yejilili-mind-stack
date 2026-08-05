#!/usr/bin/env bash
# ops/backup-after-run.sh — 运行后加密备份
# 用法: ./ops/backup-after-run.sh --run-id <uuid> [--artifact-dir <path>] [--passphrase-file <path>]
#
# 将指定 run 的快照、导出、日志和配置打包为加密备份。
# 流程：staging → manifest → tar → gzip → openssl enc → detached hash
# 不删除源数据，不触碰生产路径。

set -euo pipefail

RUN_ID=""
ARTIFACT_DIR="./staging-artifacts"
PASSPHRASE_FILE="${BACKUP_PASSPHRASE_FILE:-./secrets/backup_passphrase.txt}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --run-id) RUN_ID="$2"; shift 2 ;;
        --artifact-dir) ARTIFACT_DIR="$2"; shift 2 ;;
        --passphrase-file) PASSPHRASE_FILE="$2"; shift 2 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

if [[ -z "$RUN_ID" ]]; then
    echo "ERROR: --run-id required" >&2
    exit 1
fi

RUN_DIR="$ARTIFACT_DIR/run-${RUN_ID}"
if [[ ! -d "$RUN_DIR" ]]; then
    echo "ERROR: run directory not found: $RUN_DIR" >&2
    exit 1
fi

# ─── 禁止生产路径 ────────────────────────────────────────────────────────────
if echo "$RUN_DIR" | grep -qE '/opt/afterrain-api|/var/lib/postgresql/data'; then
    echo "ERROR: refusing to backup production path: $RUN_DIR" >&2
    exit 1
fi

# ─── 检查加密密码文件 ────────────────────────────────────────────────────────
if [[ ! -f "$PASSPHRASE_FILE" ]]; then
    echo "ERROR: passphrase file not found: $PASSPHRASE_FILE" >&2
    echo "  生成命令: openssl rand -hex 32 > $PASSPHRASE_FILE && chmod 600 $PASSPHRASE_FILE" >&2
    exit 1
fi

PASSPHRASE=$(cat "$PASSPHRASE_FILE")
if [[ -z "$PASSPHRASE" ]]; then
    echo "ERROR: passphrase file is empty: $PASSPHRASE_FILE" >&2
    exit 1
fi

BACKUP_DIR="$ARTIFACT_DIR/backups"
mkdir -p "$BACKUP_DIR"

TIMESTAMP=$(date +%Y%m%d-%H%M%S)
BACKUP_BASE="backup-${RUN_ID}-${TIMESTAMP}"
STAGING_DIR=$(mktemp -d)
trap 'rm -rf "$STAGING_DIR"' EXIT

# ─── 收集备份内容到 staging ──────────────────────────────────────────────────
echo "{\"phase\":\"backup\",\"run_id\":\"$RUN_ID\",\"action\":\"collecting\"}" >&2

# 复制 run 目录内容
rsync -a "$RUN_DIR/" "$STAGING_DIR/" 2>/dev/null || cp -r "$RUN_DIR/"* "$STAGING_DIR/" 2>/dev/null || true

# 如存在全局日志也纳入
if [[ -f "$ARTIFACT_DIR/run-index.jsonl" ]]; then
    cp "$ARTIFACT_DIR/run-index.jsonl" "$STAGING_DIR/"
fi

# 纳入 Compose 配置和 manifest（用于审计复现）
if [[ -f "docker-compose.yml" ]]; then
    cp "docker-compose.yml" "$STAGING_DIR/"
fi
if [[ -f "THIRD_PARTY_MANIFEST.json" ]]; then
    cp "THIRD_PARTY_MANIFEST.json" "$STAGING_DIR/"
fi

# ─── 生成 manifest（文件清单 + 独立 hash）─────────────────────────────────────
MANIFEST="$STAGING_DIR/backup-manifest.json"
{
    echo "{"
    echo "  \"backup_version\": \"1.0\","
    echo "  \"run_id\": \"$RUN_ID\","
    echo "  \"created_at\": \"$(date -Iseconds)\","
    echo "  \"files\": {"
    FIRST=1
    while IFS= read -r -d '' f; do
        rel="${f#$STAGING_DIR/}"
        hash=$(sha256sum "$f" | awk '{print $1}')
        if [[ "$FIRST" -eq 1 ]]; then
            FIRST=0
        else
            echo ","
        fi
        printf '    "%s": "%s"' "$rel" "$hash"
    done < <(find "$STAGING_DIR" -type f -print0)
    echo ""
    echo "  }"
    echo "}"
} > "$MANIFEST"

# ─── 打包 → 压缩 → 加密 ──────────────────────────────────────────────────────
echo "{\"phase\":\"backup\",\"run_id\":\"$RUN_ID\",\"action\":\"packing\"}" >&2

TAR_PATH="$BACKUP_DIR/${BACKUP_BASE}.tar"
TAR_GZ_PATH="$BACKUP_DIR/${BACKUP_BASE}.tar.gz"
ENC_PATH="$BACKUP_DIR/${BACKUP_BASE}.tar.gz.enc"

# 1. 未压缩 tar（保留文件权限和元数据）
tar -cf "$TAR_PATH" -C "$STAGING_DIR" .

# 2. gzip 压缩
gzip -c "$TAR_PATH" > "$TAR_GZ_PATH"
rm -f "$TAR_PATH"

# 3. openssl 对称加密（AES-256-CBC + PBKDF2）
openssl enc -aes-256-cbc -pbkdf2 -iter 100000 -salt \
    -in "$TAR_GZ_PATH" -out "$ENC_PATH" -pass "pass:$PASSPHRASE"
rm -f "$TAR_GZ_PATH"

# ─── 生成 detached hash ──────────────────────────────────────────────────────
BACKUP_HASH=$(sha256sum "$ENC_PATH" | awk '{print $1}')
echo "$BACKUP_HASH  $(basename "$ENC_PATH")" > "$ENC_PATH.sha256"

# ─── 权限最小化 ──────────────────────────────────────────────────────────────
chmod 600 "$ENC_PATH" "$ENC_PATH.sha256"

# ─── 更新 run-index.jsonl ────────────────────────────────────────────────────
INDEX="$ARTIFACT_DIR/run-index.jsonl"
mkdir -p "$(dirname "$INDEX")"
cat >> "$INDEX" <<EOF
{"run_id":"$RUN_ID","phase":"backup","backup_path":"$ENC_PATH","backup_sha256":"$BACKUP_HASH","at":"$(date -Iseconds)"}
EOF

echo "{\"phase\":\"backup\",\"run_id\":\"$RUN_ID\",\"path\":\"$ENC_PATH\",\"sha256\":\"$BACKUP_HASH\",\"status\":\"ok\"}"
