#!/usr/bin/env bash
# ops/rollback-staging.sh — 回滚 staging 迁移
# 用法: ./ops/rollback-staging.sh --run-id <uuid>
# 限制：不接受生产路径或生产 Compose project

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

# 安全限制：禁止生产路径
if [[ "$RUN_ID" == *"/opt/afterrain-api"* ]] || [[ "$RUN_ID" == *"production"* ]]; then
    echo "ERROR: rollback-staging.sh cannot operate on production paths" >&2
    exit 1
fi

echo "{\"phase\":\"rollback\",\"run_id\":\"$RUN_ID\",\"status\":\"running\"}"

# 调用 migration-cli rollback
docker compose --profile migration run --rm migration-cli \
    python -m src.main rollback --run-id "$RUN_ID" || {
    echo "{\"phase\":\"rollback\",\"run_id\":\"$RUN_ID\",\"status\":\"FAIL\"}"
    exit 1
}

echo "{\"phase\":\"rollback\",\"run_id\":\"$RUN_ID\",\"status\":\"PASS\"}"
