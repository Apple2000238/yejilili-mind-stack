#!/usr/bin/env bash
# ops/preflight.sh — 运行前检查
# 用法: ./ops/preflight.sh [--strict]

set -euo pipefail

STRICT=0
if [[ "${1:-}" == "--strict" ]]; then
    STRICT=1
fi

echo '{"phase":"preflight","time":"'"$(date -Iseconds)"'"}' >&2

ERRORS=0

# 检查 secrets 目录存在且权限正确
if [[ ! -d "./secrets" ]]; then
    echo 'ERROR: secrets/ directory missing' >&2
    ERRORS=$((ERRORS + 1))
fi

for f in mcp_adapter_token.txt postgres_password.txt; do
    if [[ ! -f "./secrets/$f" ]]; then
        echo "WARNING: secrets/$f missing" >&2
        [[ "$STRICT" == 1 ]] && ERRORS=$((ERRORS + 1))
    fi
done

# 检查 upstream 源码存在
if [[ ! -f "./upstream/nocturne-memory-core/server.py" ]]; then
    echo 'ERROR: Nocturne upstream source missing. Run: ./ops/import-upstream.sh' >&2
    ERRORS=$((ERRORS + 1))
fi

if [[ ! -f "./upstream/xinchao-dynamic-mind/src/server.js" ]]; then
    echo 'ERROR: XinChao upstream source missing. Run: ./ops/import-upstream.sh' >&2
    ERRORS=$((ERRORS + 1))
fi

# 检查 docker-compose.yml digest 已锁定
if grep -q 'sha256:TO_BE_LOCKED' docker-compose.yml; then
    echo 'ERROR: postgres digest not locked in docker-compose.yml' >&2
    ERRORS=$((ERRORS + 1))
fi

# 检查 THIRD_PARTY_MANIFEST 完整性
if [[ ! -f "THIRD_PARTY_MANIFEST.json" ]]; then
    echo 'ERROR: THIRD_PARTY_MANIFEST.json missing' >&2
    ERRORS=$((ERRORS + 1))
fi

if [[ "$ERRORS" -gt 0 ]]; then
    echo "{\"phase\":\"preflight\",\"status\":\"FAIL\",\"errors\":$ERRORS}" >&2
    exit 1
fi

echo '{"phase":"preflight","status":"PASS"}' >&2
exit 0
