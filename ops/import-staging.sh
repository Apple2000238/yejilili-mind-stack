#!/usr/bin/env bash
# ops/import-staging.sh — 导入已验证的 source manifest 到 staging
# 用法: ./ops/import-staging.sh --run-id <uuid> [--mapping-version v1]

set -euo pipefail

RUN_ID=""
MAPPING_VERSION="v1"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --run-id) RUN_ID="$2"; shift 2 ;;
        --mapping-version) MAPPING_VERSION="$2"; shift 2 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

if [[ -z "$RUN_ID" ]]; then
    echo "ERROR: --run-id required" >&2
    exit 1
fi

echo "{\"phase\":\"import\",\"run_id\":\"$RUN_ID\",\"mapping_version\":\"$MAPPING_VERSION\",\"status\":\"running\"}"

# 调用 migration-cli import-staging
docker compose --profile migration run --rm migration-cli \
    python -m src.main import-staging --run-id "$RUN_ID" --mapping-version "$MAPPING_VERSION" || {
    echo "{\"phase\":\"import\",\"run_id\":\"$RUN_ID\",\"status\":\"FAIL\"}"
    exit 1
}

echo "{\"phase\":\"import\",\"run_id\":\"$RUN_ID\",\"status\":\"PASS\"}"
