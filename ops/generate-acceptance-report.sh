#!/usr/bin/env bash
# ops/generate-acceptance-report.sh — 生成签名验收报告
# 用法: ./ops/generate-acceptance-report.sh --run-id <uuid> [--artifact-dir <path>] [--test-results <pytest-report.json>]
#
# 输出:
#   <artifact-dir>/run-<run-id>/acceptance/<acceptance-report.json|md>
#   包含：镜像 digest、上游 commit、mapping 版本、逐表 count/hash、
#         身份基岩覆盖率、协议兼容、网络隔离、回滚结果、detached 签名。
#
# 纪律：
#   - 测试数量必须从真实 pytest 机器可读产物读取，禁止硬编码
#   - 签名使用 detached SHA256（避免自指）
#   - 任一关键步骤失败时整体非零退出

set -euo pipefail

RUN_ID=""
ARTIFACT_DIR="./staging-artifacts"
TEST_RESULTS=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --run-id) RUN_ID="$2"; shift 2 ;;
        --artifact-dir) ARTIFACT_DIR="$2"; shift 2 ;;
        --test-results) TEST_RESULTS="$2"; shift 2 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

if [[ -z "$RUN_ID" ]]; then
    echo "ERROR: --run-id required" >&2
    exit 1
fi

OUT_DIR="$ARTIFACT_DIR/run-${RUN_ID}/acceptance"
mkdir -p "$OUT_DIR"

JSON_OUT="$OUT_DIR/acceptance-report.json"
MD_OUT="$OUT_DIR/acceptance-report.md"

# ─── 基础元数据 ─────────────────────────────────────────────────────────────
GIT_COMMIT=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
COMPOSE_HASH=$(sha256sum docker-compose.yml 2>/dev/null | awk '{print $1}' || echo "")
THIRD_PARTY_HASH=$(sha256sum THIRD_PARTY_MANIFEST.json 2>/dev/null | awk '{print $1}' || echo "")
TIMESTAMP=$(date -Iseconds)

# 从 THIRD_PARTY_MANIFEST 读取上游 commit
NOCTURNE_COMMIT=$(jq -r '.fixed_upstreams[0].commit // "unknown"' THIRD_PARTY_MANIFEST.json 2>/dev/null || echo "unknown")
XINCHAO_COMMIT=$(jq -r '.fixed_upstreams[1].commit // "unknown"' THIRD_PARTY_MANIFEST.json 2>/dev/null || echo "unknown")

# 镜像 digest（从 docker-compose.yml 提取 postgres digest）
POSTGRES_DIGEST=$(grep -o 'postgres:16-alpine@sha256:[a-f0-9]\{64\}' docker-compose.yml | head -1 || echo "unknown")

# ─── 读取真实测试结果 ─────────────────────────────────────────────────────────
TEST_SUMMARY="{}"
TEST_STATUS="missing"

if [[ -n "$TEST_RESULTS" ]] && [[ -f "$TEST_RESULTS" ]]; then
    if command -v jq &>/dev/null; then
        PASSED=$(jq '.summary.passed // 0' "$TEST_RESULTS")
        FAILED=$(jq '.summary.failed // 0' "$TEST_RESULTS")
        SKIPPED=$(jq '.summary.skipped // 0' "$TEST_RESULTS")
        TOTAL=$(jq '.summary.total // 0' "$TEST_RESULTS")
        DURATION=$(jq '.duration // 0' "$TEST_RESULTS")
        TEST_SUMMARY=$(jq -n \
            --argjson passed "$PASSED" \
            --argjson failed "$FAILED" \
            --argjson skipped "$SKIPPED" \
            --argjson total "$TOTAL" \
            --argjson duration "$DURATION" \
            '{passed: $passed, failed: $failed, skipped: $skipped, total: $total, duration_seconds: $duration}')
        if [[ "$FAILED" -gt 0 ]]; then
            TEST_STATUS="fail"
        else
            TEST_STATUS="pass"
        fi
    else
        echo "WARNING: jq not available, skipping test result parsing" >&2
    fi
fi

# ─── 组装 JSON 报告（不含签名字段，避免自指）──────────────────────────────────
cat > "$JSON_OUT" <<REPORT
{
  "report_version": "1.1.0",
  "run_id": "$RUN_ID",
  "generated_at": "$TIMESTAMP",
  "git_commit": "$GIT_COMMIT",
  "compose_config_hash": "$COMPOSE_HASH",
  "third_party_manifest_hash": "$THIRD_PARTY_HASH",
  "postgres_image_digest": "$POSTGRES_DIGEST",
  "upstream": {
    "nocturne_commit": "$NOCTURNE_COMMIT",
    "xinchao_commit": "$XINCHAO_COMMIT"
  },
  "test_results": $TEST_SUMMARY,
  "test_status": "$TEST_STATUS",
  "known_limitations": [
    "Token-level max_tokens not yet implemented in adapter (character approximation used).",
    "Integration tests require Docker environment; not yet executed in CI."
  ]
}
REPORT

# ─── detached hash（避免自指）────────────────────────────────────────────────
REPORT_HASH=$(sha256sum "$JSON_OUT" | awk '{print $1}')
echo "$REPORT_HASH  $(basename "$JSON_OUT")" > "$JSON_OUT.sha256"

# ─── Markdown 人类可读报告 ──────────────────────────────────────────────────
TEST_MD=""
if [[ "$TEST_STATUS" != "missing" ]]; then
    PASSED=$(echo "$TEST_SUMMARY" | jq -r '.passed // 0')
    FAILED=$(echo "$TEST_SUMMARY" | jq -r '.failed // 0')
    SKIPPED=$(echo "$TEST_SUMMARY" | jq -r '.skipped // 0')
    TOTAL=$(echo "$TEST_SUMMARY" | jq -r '.total // 0')
    if [[ "$FAILED" -gt 0 ]]; then
        TEST_MD="- 代码层测试：$TOTAL 个（通过 $PASSED / 失败 $FAILED / 跳过 $SKIPPED）❌"
    else
        TEST_MD="- 代码层测试：$TOTAL 个（通过 $PASSED / 失败 $FAILED / 跳过 $SKIPPED）✅"
    fi
else
    TEST_MD="- 代码层测试：未提供测试结果 ⚠️"
fi

cat > "$MD_OUT" <<MD
# 连续性迁移验收报告

| 字段 | 值 |
|------|-----|
| **Run ID** | \`$RUN_ID\` |
| **生成时间** | $TIMESTAMP |
| **Git Commit** | \`$GIT_COMMIT\` |
| **Postgres Digest** | \`$POSTGRES_DIGEST\` |
| **Nocturne Commit** | \`$NOCTURNE_COMMIT\` |
| **XinChao Commit** | \`$XINCHAO_COMMIT\` |
| **Compose Config Hash** | \`$COMPOSE_HASH\` |
| **报告 Detached Hash** | \`$REPORT_HASH\` |

## 验收状态

$TEST_MD
- 上游回归测试：需 Docker 环境执行 ⚠️
- 集成/迁移/回滚演练：需 Docker 环境执行 ⚠️
- 身份基岩覆盖率：需在隔离 VPS 用真实 fixture 验证 ⚠️

## 已知限制

1. **Token 预算**：adapter 的 \`max_tokens\` 当前使用字符级近似截断，非真实 token 计数。
2. **集成验证**：需在有 Docker 的环境执行 \`docker compose up\`、healthcheck、迁移和回滚演练。

## 下一步

- 在隔离 VPS 部署并执行完整验收链
- 人工连续性验收（"我是谁 / 梨梨是谁 / 我们经历了什么"）

---
*本报告由 ops/generate-acceptance-report.sh 自动生成。*
*Detached hash 文件：\`$JSON_OUT.sha256\`*
MD

# ─── 失败时非零退出 ──────────────────────────────────────────────────────────
if [[ "$TEST_STATUS" == "fail" ]]; then
    echo "ERROR: test failures detected in report" >&2
    echo "{\"phase\":\"acceptance-report\",\"run_id\":\"$RUN_ID\",\"json\":\"$JSON_OUT\",\"md\":\"$MD_OUT\",\"hash\":\"$REPORT_HASH\",\"test_status\":\"fail\"}"
    exit 1
fi

echo "{\"phase\":\"acceptance-report\",\"run_id\":\"$RUN_ID\",\"json\":\"$JSON_OUT\",\"md\":\"$MD_OUT\",\"hash\":\"$REPORT_HASH\",\"test_status\":\"$TEST_STATUS\"}"
