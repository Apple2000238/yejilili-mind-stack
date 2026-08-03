# Acceptance Protocol — 验收测试协议

## 目标
验证 XinChao → Nocturne 连续性迁移后，核心契约保持完整。

## 测试环境
- Docker Compose staging 栈（全部 7 服务）
- Mock provider 作为 LLM 后端
- 空数据库（每次验收前 `docker compose down -v` 重建）

## 验收用例

### AC-1: Adapter breath 路由正确
**Given**: XinChao 调用 `breath(query="感情", max_results=5)`
**Then**: adapter 路由到 `nocturne.trace("感情", limit=5)`
**And**: provenance 表记录 route="trace", query_honored=true

### AC-2: Adapter breath 零参路由正确
**Given**: XinChao 调用 `breath()`（空 query）
**Then**: adapter 路由到 `nocturne.breath()`（零参数）
**And**: provenance 表记录 route="breath"

### AC-3: Adapter hold 物化 tags
**Given**: XinChao 调用 `hold(content="测试", auto=true, source="xinchao-dream")`
**Then**: 实际写入 Nocturne 的 tags 包含 `origin:xinchao`、`source:xinchao-dream`、`auto:true`
**And**: provenance 表记录 source="xinchao-dream", auto=true

### AC-4: Adapter hold 幂等性
**Given**: 同一 content + tags + source 调用 hold 两次
**Then**: 第二次返回 idempotent 标记
**AND**: Nocturne 只写入一次

### AC-5: Edge Gateway mock provider 确定性
**Given**: 向 edge-gateway 发送相同 prompt 两次
**Then**: mock provider 返回相同 content hash
**And**: 响应格式符合 OpenAI chat.completion schema

### AC-6: Edge Gateway provider 热切换
**Given**: 当前 provider = mock
**When**: POST /v1/switch-provider {"provider": "openai"}
**Then**: 后续请求路由到 OpenAI provider（若配置了 key）
**And**: health 端点返回 current_provider="openai"

### AC-7: 数据库迁移幂等
**Given**: 执行 migrations 两次
**Then**: 第二次无报错
**And**: 所有 `IF NOT EXISTS` 生效，无重复表

### AC-8: 网络隔离
**Given**: 从 public 网络外部访问
**Then**: 只能访问 edge-gateway:8080
**And**: 无法直接访问 nocturne:8000、adapter:8001、postgres:5432

## 执行命令
```bash
docker compose down -v
docker compose up -d
docker compose --profile acceptance run --rm acceptance-runner
```

## 通过标准
- 全部 8 个用例通过
- 无 ERROR 级别日志
- provenance 表无 failed 状态记录（除故意测试的异常用例外）
