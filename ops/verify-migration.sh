#!/usr/bin/env bash
# ops/verify-migration.sh — 校验迁移结果
# 用法: ./ops/verify-migration.sh --run-id <uuid>

set -euo pipefail

RUN_ID=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --run-id) RUN_ID="$2"; shift 2 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

if [[ -z "$RUN_ID" ]]; then
    echo "ERROR: --run-id required" >&2
    exit 1
fi

echo "{\"phase\":\"verify\",\"run_id\":\"$RUN_ID\",\"status\":\"running\"}"

# 调用 migration-cli verify
docker compose --profile migration run --rm migration-cli \
    python -m src.main verify --run-id "$RUN_ID" || {
    echo "{\"phase\":\"verify\",\"run_id\":\"$RUN_ID\",\"status\":\"FAIL\"}"
    exit 1
}

echo "{\"phase\":\"verify\",\"run_id\":\"$RUN_ID\",\"status\":\"PASS\"}"
