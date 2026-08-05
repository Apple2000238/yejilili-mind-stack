# Acceptance Protocol — 验收测试协议

## 目标
验证 XinChao → Nocturne 连续性迁移后，核心契约保持完整。

## 测试环境
- Docker Compose staging 栈（全部 7 个服务）
- Mock provider 作为 LLM 后端
- 空数据库（每次验收前 `docker compose down -v` 重建）

## 验收用例

### AC-1: 服务健康检查
**Given**: staging 栈已启动  
**When**: 访问 gateway `/health` 和 adapter `/health`  
**Then**: 两者均返回 HTTP 200  
**And**: 响应体包含预期的健康状态字段

### AC-2: MCP 工具列表与 Schema
**Given**: adapter 已启动且 MCP token 有效  
**When**: POST `/mcp` {"jsonrpc":"2.0","method":"tools/list"}  
**Then**: 返回 tools 列表包含 `breath` 和 `hold`  
**And**: `hold` 的 inputSchema 包含 `auto` 和 `source` 字段

### AC-3: breath query 路由与截断
**Given**: adapter 的 MCP tools/call 接口可用  
**When**: 调用 `breath(query="感情", max_results=5, max_tokens=1000)`  
**Then**: 带 query 时路由为 `trace`  
**And**: 空 query 时路由为 `breath`  
**And**: metadata 包含 `route` 和 `query_honored` 字段

### AC-4: hold 幂等性与 Provenance
**Given**: 调用 `hold(content="acceptance test memory", tags="test,acceptance", importance=3, auto=True, source="xinchao-dream")`  
**When**: 使用相同 payload 再次调用  
**Then**: 两次返回相同的 `target_ref`  
**And**: provenance 不重复写入

### AC-5: 网络隔离
**Given**: staging 栈的网络配置  
**Then**: Nocturne (port 8000) 不暴露公网端口  
**And**: adapter (port 8001) 是唯一能访问 Nocturne 的路径  
**And**: gateway (port 8002) 从内部网络可达

### AC-6: OpenAI/Anthropic 协议兼容
**Given**: edge-gateway 已启动  
**When**: POST `/v1/chat/completions`（OpenAI 格式）  
**And**: POST `/v1/messages`（Anthropic 格式）  
**Then**: OpenAI 响应包含 `choices` 和 `usage`  
**And**: Anthropic 响应包含 `content` 或兼容结构

### AC-7: 会话 ID 稳定性
**Given**: edge-gateway 已启动  
**When**: 使用相同 `session_id` 发送两次请求  
**To**: `/v1/chat/completions` 和 `/v1/messages`  
**Then**: 两次请求均返回 HTTP 200（无 500 错误）  
**And**: session 映射保持一致

### AC-8: 日志脱敏
**Given**: adapter 的审计日志目录 `/var/log/adapter` 已挂载  
**When**: 检查 `*.log` 文件内容  
**Then**: 日志中不得出现 `Authorization`、`Bearer `、原始聊天内容、`secret`、`api_key` 等敏感模式  
**And**: 环境变量中不得泄露 `OPENAI_API_KEY`、`ANTHROPIC_API_KEY`、`MCP_ADAPTER_TOKEN`

> **注意**：若 `/var/log/adapter` 目录不存在或为空，视为审计基础设施缺失，AC-8 标记为失败。

## 执行命令

```bash
docker compose down -v
docker compose up -d
docker compose --profile acceptance run --rm acceptance-runner
```

## 通过标准
- 全部 8 个用例通过（`passed == True`）
- 无 ERROR 级别日志
- 验收报告 JSON 和 Markdown 写入 `/artifacts/`
- AC-8 中 audit 日志目录必须存在且非空
