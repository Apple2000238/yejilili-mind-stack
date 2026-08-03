# SECURITY.md

## 威胁模型

本系统处理梨梨与夜霁的连续性记忆数据，安全目标是：
- **保密性**：聊天原文、身份数据、关系语料不得泄露到未授权方
- **完整性**：迁移数据不可被静默篡改或覆盖
- **可用性**：连续性服务在故障时可回滚，不丢失源数据
- **不可否认性**：所有操作有 provenance 审计记录

## 网络边界

| 网络 | 可访问服务 | 禁止暴露 |
|------|-----------|---------|
| public | edge-gateway（唯一端口） | 其他所有服务 |
| mind | edge-gateway, xinchao, nocturne-adapter, continuity-ledger | Nocturne 直连 |
| nocturne-private | nocturne-adapter, nocturne | 任何其他服务 |

- Nocturne `/mcp` 不依赖 Cookie 登录保护，网络隔离是必需控制
- 禁止 `allow_origins=["*"]`

## Secret 管理

- 所有密钥通过 Docker secrets 或运行时环境变量注入
- `.env.example` 只包含变量名和占位符，不含真实值
- 禁止将 API key、token、密码写入 Git、镜像层、日志或测试 fixture
- 日志中不得出现 Authorization header、完整 messages 或聊天原文

## 认证

- MCP adapter 要求 `Authorization: Bearer <MIND_ADAPTER_TOKEN>`
- edge-gateway 和 XinChao 使用独立的最小权限 token
- adapter 记录调用方 subject，不共享万能 token

## 日志脱敏

日志记录：
- ✅ 允许：hash、长度、token 数、模型、状态码、结构化审计字段
- ❌ 禁止：secret 值、聊天原文、Authorization header、完整 response

## 备份加密

- 导出快照和 staging artifact 加密存放
- 权限最小化（600 或更严格）
- 真实 AR 数据永不进入 GitHub/Kimi 远端日志

## 已知限制

- 当前为 staging-only 配置，未连接生产数据源
- LLM provider 使用 mock/staging 凭据，生产切换需独立配置
