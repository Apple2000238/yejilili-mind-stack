#!/usr/bin/env bash
# ops/snapshot.sh — 创建 pre/post snapshot
# 用法:
#   ./ops/snapshot.sh pre --run-id <uuid> [--source-db <path>]
#   ./ops/snapshot.sh post --run-id <uuid> --exit-code <n> [--error-summary <text>]

set -euo pipefail

PHASE="${1:-}"
shift || true

RUN_ID=""
SOURCE_DB=""
EXIT_CODE=0
ERROR_SUMMARY=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --run-id) RUN_ID="$2"; shift 2 ;;
        --source-db) SOURCE_DB="$2"; shift 2 ;;
        --exit-code) EXIT_CODE="$2"; shift 2 ;;
        --error-summary) ERROR_SUMMARY="$2"; shift 2 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

if [[ -z "$RUN_ID" ]]; then
    echo "ERROR: --run-id required" >&2
    exit 1
fi

SNAPSHOT_DIR="./staging-artifacts/run-${RUN_ID}/${PHASE}"
mkdir -p "$SNAPSHOT_DIR"

MANIFEST="$SNAPSHOT_DIR/snapshot-manifest.json"

if [[ "$PHASE" == "pre" ]]; then
    GIT_COMMIT=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
    COMPOSE_HASH=$(sha256sum docker-compose.yml 2>/dev/null | awk '{print $1}')
    cat > "$MANIFEST" <<EOF
{
  "snapshot_type": "pre",
  "run_id": "$RUN_ID",
  "git_commit": "$GIT_COMMIT",
  "compose_config_hash": "$COMPOSE_HASH",
  "source_db": "$SOURCE_DB",
  "timestamp": "$(date -Iseconds)"
}
EOF
elif [[ "$PHASE" == "post" ]]; then
    cat > "$MANIFEST" <<EOF
{
  "snapshot_type": "post",
  "run_id": "$RUN_ID",
  "exit_code": $EXIT_CODE,
  "error_summary": "$ERROR_SUMMARY",
  "timestamp": "$(date -Iseconds)"
}
EOF
else
    echo "ERROR: phase must be 'pre' or 'post'" >&2
    exit 1
fi

echo "{\"phase\":\"snapshot\",\"type\":\"$PHASE\",\"path\":\"$MANIFEST\"}"
