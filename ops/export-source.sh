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

if [[ -z "$SOURCE_DB" ]] || [[ -z "$RUN_ID" ]]; then
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

echo "{\"phase\":\"export\",\"source_db\":\"$SOURCE_DB\",\"run_id\":\"$RUN_ID\",\"status\":\"running\"}"

# 调用 migration-cli export-source
docker compose --profile migration run --rm migration-cli \
    python -m src.main export-source --source-db "$SOURCE_DB" --run-id "$RUN_ID" || {
    echo "{\"phase\":\"export\",\"run_id\":\"$RUN_ID\",\"status\":\"FAIL\"}"
    exit 1
}

echo "{\"phase\":\"export\",\"run_id\":\"$RUN_ID\",\"status\":\"PASS\"}"
