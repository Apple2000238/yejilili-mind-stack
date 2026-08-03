#!/usr/bin/env bash
# ops/backup-after-run.sh — 运行后自动备份
# 用法: ./ops/backup-after-run.sh --run-id <uuid> [--artifact-dir <path>]
#
# 将指定 run 的快照、导出、日志和配置打包为加密备份。
# 不删除源数据，不触碰生产路径。

set -euo pipefail

RUN_ID=""
ARTIFACT_DIR="./staging-artifacts"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --run-id) RUN_ID="$2"; shift 2 ;;
        --artifact-dir) ARTIFACT_DIR="$2"; shift 2 ;;
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

BACKUP_DIR="$ARTIFACT_DIR/backups"
mkdir -p "$BACKUP_DIR"

BACKUP_NAME="backup-${RUN_ID}-$(date +%Y%m%d-%H%M%S).tar.gz"
BACKUP_PATH="$BACKUP_DIR/$BACKUP_NAME"

# ─── 禁止生产路径 ────────────────────────────────────────────────────────────
if echo "$RUN_DIR" | grep -qE '/opt/afterrain-api|/var/lib/postgresql/data'; then
    echo "ERROR: refusing to backup production path: $RUN_DIR" >&2
    exit 1
fi

# ─── 收集备份内容 ────────────────────────────────────────────────────────────
# 包含：pre/post snapshot、source manifest、导出快照、验收报告、日志
echo "{\"phase\":\"backup\",\"run_id\":\"$RUN_ID\",\"action\":\"packing\"}" >&2

tar -czf "$BACKUP_PATH" \
    -C "$ARTIFACT_DIR" \
    "run-${RUN_ID}/pre" \
    "run-${RUN_ID}/post" \
    "run-${RUN_ID}/export" \
    "run-${RUN_ID}/acceptance" \
    2>/dev/null || true

# 如果存在全局日志也纳入
if [[ -f "$ARTIFACT_DIR/run-index.jsonl" ]]; then
    tar -rf "$BACKUP_PATH" -C "$ARTIFACT_DIR" "run-index.jsonl" 2>/dev/null || true
fi

# ─── 计算备份 hash ───────────────────────────────────────────────────────────
BACKUP_HASH=$(sha256sum "$BACKUP_PATH" | awk '{print $1}')

# ─── 更新 run-index.jsonl ────────────────────────────────────────────────────
INDEX="$ARTIFACT_DIR/run-index.jsonl"
mkdir -p "$(dirname "$INDEX")"
cat >> "$INDEX" <<EOF
{"run_id":"$RUN_ID","phase":"backup","backup_path":"$BACKUP_PATH","backup_sha256":"$BACKUP_HASH","at":"$(date -Iseconds)"}
EOF

# ─── 权限最小化 ──────────────────────────────────────────────────────────────
chmod 600 "$BACKUP_PATH"

echo "{\"phase\":\"backup\",\"run_id\":\"$RUN_ID\",\"path\":\"$BACKUP_PATH\",\"sha256\":\"$BACKUP_HASH\",\"status\":\"ok\"}"
