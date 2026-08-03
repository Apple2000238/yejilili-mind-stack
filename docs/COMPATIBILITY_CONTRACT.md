# COMPATIBILITY_CONTRACT.md

## 外部兼容面

### OpenAI 兼容接口

- `POST /v1/chat/completions`
- 支持流式（`stream: true`）与非流式
- 响应包含 `id`, `object`, `choices`, `usage`
- 错误返回标准 HTTP status + JSON detail

### Anthropic 兼容接口

- `POST /v1/messages`
- 支持流式与非流式
- 系统消息通过 `system` 字段传递
- 响应包含 `id`, `type`, `role`, `content`, `usage`

### 会话 ID 语义

优先级：
1. 请求 body 中的 `session_id`
2. 请求 body 中的 `sessionId`
3. metadata 中的对应字段
4. 专用 header
5. 缺失时生成稳定的新 UUID

实际选择的来源必须记入结构化审计日志。

## 内部兼容面

### MCP Streamable HTTP

- 入口：`POST /mcp`
- 支持方法：`initialize`, `tools/list`, `tools/call`
- 鉴权：`Authorization: Bearer <token>`
- 错误格式：JSON-RPC 2.0，code -32601~-32603

### Adapter 工具契约

**breath**
- 输入：`query?: string`, `max_results?: integer(1-20)`, `max_tokens?: integer(100-4000)`
- 路由：非空 query → trace(query, limit)；空 query → breath()
- 返回 metadata 包含：`route`, `query_honored`, `requested/applied_max_results`, `requested/applied_max_tokens`, `truncated`

**hold**
- 输入：`content: string`, `tags?: string`, `importance?: integer(1-10)`, `auto?: boolean`, `source?: string`
- source 白名单：`xinchao-dream`, `xinchao-handoff`, `xinchao-thought`, `xinchao-heartbeat`, `edge-gateway`, `migration-cli`
- 标签追加：`origin:xinchao`, `source:<source>`, `auto:true|false`
- 幂等：以 `(event_id, tool_name, input_hash)` 为键，重复请求返回第一次的 target_ref

## 版本策略

- mapping_version 当前为 `v1`
- 新版本的 mapping 必须向后兼容或显式标记为 breaking change
- 任何 schema 变更需新增迁移文件，不得修改已执行的迁移
