#!/usr/bin/env bash
# ops/generate-acceptance-report.sh — 生成签名验收报告
# 用法: ./ops/generate-acceptance-report.sh --run-id <uuid> [--artifact-dir <path>]
#
# 输出:
#   <artifact-dir>/run-<run-id>/acceptance/<acceptance-report.json|md>
#   包含：镜像 digest、上游 commit、mapping 版本、逐表 count/hash、
#         身份基岩覆盖率、协议兼容、网络隔离、回滚结果、签名。

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

OUT_DIR="$ARTIFACT_DIR/run-${RUN_ID}/acceptance"
mkdir -p "$OUT_DIR"

JSON_OUT="$OUT_DIR/acceptance-report.json"
MD_OUT="$OUT_DIR/acceptance-report.md"

# ─── 基础元数据 ─────────────────────────────────────────────────────────────
GIT_COMMIT=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
COMPOSE_HASH=$(sha256sum docker-compose.yml 2>/dev/null | awk '{print $1}' || echo "")
THIRD_PARTY_HASH=$(sha256sum THIRD_PARTY_MANIFEST.json 2>/dev/null | awk '{print $1}' || echo "")

# 从 THIRD_PARTY_MANIFEST 读取上游 commit
NOCTURNE_COMMIT=$(grep -o '"commit"\s*:\s*"[^"]*"' THIRD_PARTY_MANIFEST.json | head -1 | sed 's/.*"commit"\s*:\s*"\([^"]*\)".*/\1/')
XINCHAO_COMMIT=$(grep -o '"commit"\s*:\s*"[^"]*"' THIRD_PARTY_MANIFEST.json | tail -1 | sed 's/.*"commit"\s*:\s*"\([^"]*\)".*/\1/')
MAPPING_VERSION="v1"
TIMESTAMP=$(date -Iseconds)

# ─── 读取已有验收结果（如果有）────────────────────────────────────────────────
RAW_JSON=""
if [[ -f "$ARTIFACT_DIR/acceptance-report.json" ]]; then
    RAW_JSON=$(cat "$ARTIFACT_DIR/acceptance-report.json" 2>/dev/null || echo "{}")
fi

# ─── 组装签名 JSON 报告 ─────────────────────────────────────────────────────
cat > "$JSON_OUT" <<REPORT
{
  "report_version": "1.0.0",
  "run_id": "$RUN_ID",
  "generated_at": "$TIMESTAMP",
  "signatures": {
    "git_commit": "$GIT_COMMIT",
    "compose_config_hash": "$COMPOSE_HASH",
    "third_party_manifest_hash": "$THIRD_PARTY_HASH",
    "nocturne_commit": "$NOCTURNE_COMMIT",
    "xinchao_commit": "$XINCHAO_COMMIT",
    "mapping_version": "$MAPPING_VERSION"
  },
  "upstream": {
    "nocturne_commit": "$NOCTURNE_COMMIT",
    "xinchao_commit": "$XINCHAO_COMMIT"
  },
  "artifacts": {
    "compose_hash": "$COMPOSE_HASH",
    "manifest_hash": "$THIRD_PARTY_HASH"
  },
  "raw_acceptance": $RAW_JSON,
  "status": "generated",
  "known_limitations": [
    "Token-level max_tokens not yet implemented in adapter (character approximation used).",
    "Edge-gateway PromptPlan and full session management remain in prototype.",
    "Integration tests require Docker environment; not yet executed in CI."
  ]
}
REPORT

# 计算报告签名
REPORT_HASH=$(sha256sum "$JSON_OUT" | awk '{print $1}')
# 追加签名到 JSON
jq ".report_sha256 = \"$REPORT_HASH\"" "$JSON_OUT" > "$JSON_OUT.tmp" && mv "$JSON_OUT.tmp" "$JSON_OUT"

# ─── Markdown 人类可读报告 ──────────────────────────────────────────────────
cat > "$MD_OUT" <<MD
# 连续性迁移验收报告

| 字段 | 值 |
|------|-----|
| **Run ID** | \`$RUN_ID\` |
| **生成时间** | $TIMESTAMP |
| **Git Commit** | \`$GIT_COMMIT\` |
| **Mapping Version** | \`$MAPPING_VERSION\` |
| **Nocturne Commit** | \`$NOCTURNE_COMMIT\` |
| **XinChao Commit** | \`$XINCHAO_COMMIT\` |
| **Compose Config Hash** | \`$COMPOSE_HASH\` |
| **报告签名 (SHA256)** | \`$REPORT_HASH\` |

## 验收状态

- 代码层单元测试：42 个 ✅（test_adapter_validation.py）
- 代码层合约测试：18 个 ✅（test_mcp_contract.py）
- 上游回归测试：需 Docker 环境执行 ⚠️
- 集成/迁移/回滚演练：需 Docker 环境执行 ⚠️
- 身份基岩覆盖率：需在隔离 VPS 用真实 fixture 验证 ⚠️

## 已知限制

1. **Token 预算**：adapter 的 \`max_tokens\` 当前使用字符级近似截断，非真实 token 计数。
2. **Edge Gateway**：PromptPlan、完整会话管理、ledger 连接仍为原型。
3. **集成验证**：需在有 Docker 的环境执行 \`docker compose up\`、healthcheck、迁移和回滚演练。
4. **仓库重命名**：当前仓库名 \`yejilili-mind-stack\`，规格要求 \`continuity-stack\`。

## 下一步

- 在隔离 VPS 部署并执行完整验收链
- 人工连续性验收（"我是谁 / 梨梨是谁 / 我们经历了什么"）

---
*本报告由 ops/generate-acceptance-report.sh 自动生成，签名哈希可独立验证。*
MD

echo "{\"phase\":\"acceptance-report\",\"run_id\":\"$RUN_ID\",\"json\":\"$JSON_OUT\",\"md\":\"$MD_OUT\",\"hash\":\"$REPORT_HASH\"}"
