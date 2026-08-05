#!/usr/bin/env bash
# ops/export-source.sh — 从 SQLite source 导出一致性快照
# 用法: ./ops/export-source.sh --source-db <absolute-path> --run-id <uuid> --read-only

set -euo pipefail

SOURCE_DB=""
RUN_ID=""
READ_ONLY=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --source-db) SOURCE_DB="$2"; shift 2 ;;
        --run-id) RUN_ID="$2"; shift 2 ;;
        --read-only) READ_ONLY=1; shift ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

if [[ -z "${SOURCE_DB:-}" ]] || [[ -z "${RUN_ID:-}" ]]; then
    echo "ERROR: --source-db and --run-id required" >&2
    exit 1
fi

if [[ "$READ_ONLY" != 1 ]]; then
    echo "ERROR: must specify --read-only" >&2
    exit 1
fi

if [[ ! "$SOURCE_DB" = /* ]]; then
    echo "ERROR: source-db must be absolute path" >&2
    exit 1
fi

if [[ ! -f "$SOURCE_DB" ]]; then
    echo "ERROR: source db not found: $SOURCE_DB" >&2
    exit 1
fi

if [[ "$SOURCE_DB" == "/opt/afterrain-api"* ]]; then
    echo "ERROR: cannot export from production path directly. Create a backup copy first." >&2
    exit 1
fi

# 校验 SQLite 可读性
if ! file "$SOURCE_DB" | grep -qi sqlite; then
    echo "ERROR: source db does not appear to be a SQLite database: $SOURCE_DB" >&2
    exit 1
fi

# 容器内挂载路径（只读）
CONTAINER_DB_PATH="/tmp/source.db"

echo "{\"phase\":\"export\",\"source_db\":\"$SOURCE_DB\",\"run_id\":\"$RUN_ID\",\"status\":\"running\"}"

# 调用 migration-cli export-source，通过 -v 将宿主文件只读挂载到容器
docker compose --profile migration run --rm \
    -v "$SOURCE_DB:$CONTAINER_DB_PATH:ro" \
    migration-cli \
    python -m src.main export-source --source-db "$CONTAINER_DB_PATH" --run-id "$RUN_ID" || {
    echo "{\"phase\":\"export\",\"run_id\":\"$RUN_ID\",\"status\":\"FAIL\"}"
    exit 1
}

echo "{\"phase\":\"export\",\"run_id\":\"$RUN_ID\",\"status\":\"PASS\"}"
