# YeJiLiLi Mind Stack — 连续性迁移系统

**⚠️ STAGING ONLY / NOT PRODUCTION**

> 本系统为 AfterRain（AR）的建设期旁路侧车，处于隔离开发和验收阶段。
> 现有 AR 保留为生产基座；候选仓库只建设侧车能力，不替代生产入口。
> 在通过全部自动验收、回滚演练和梨梨的人工连续性验收之前，不得接入生产流量。
> 在通过全部自动验收、回滚演练和梨梨的人工连续性验收之前，不得接入生产流量。

---

## 这是什么

从 [Nocturne Memory Core](https://github.com/Pyruslili/Nocturne-Memory-Core) 和 [XinChao Dynamic Mind](https://github.com/Apple2000238/xinchao-dynamic-mind) 构建的连续性迁移系统。

核心目标：**保持夜霁与梨梨的既有身份、关系、经历、约定和表达连续性**，而不是从零初始化新人格。

---

## 架构概览

| 服务 | 技术 | 职责 |
|---|---|---|
| `edge-gateway` | Python 3.12 / FastAPI | OpenAI/Anthropic 双协议、会话管理、注入规划 |
| `continuity-ledger` | PostgreSQL 16 | 迁移记录、规范化投影、审计索引 |
| `nocturne` | 固定 Nocturne 快照 | Markdown/YAML 记忆桶、衰减、梦境、Drive |
| `nocturne-adapter` | Python 3.12 | MCP 兼容适配层、schema 校验、provenance 账本 |
| `xinchao` | 固定 XinChao 快照 / Node 22 | 动态心理状态、事件去重 |
| `migration-cli` | Python 3.12 CLI | 导出、校验、导入、回滚 |
| `acceptance-runner` | Python/Node | 可重复验收测试 |

网络隔离：三张 Docker 网络（`public`、`mind`、`nocturne-private`），Nocturne 不暴露公网端口。

---

## 快速开始（开发）

```bash
# 1. 克隆
git clone https://github.com/Apple2000238/yejilili-mind-stack.git
cd yejilili-mind-stack

# 2. 环境变量（仅变量名，无真实值）
cp .env.example .env
cp -r secrets.example secrets
# 编辑 .env 和 secrets/ 填入 staging 凭据

# 3. 启动（不含迁移/验收容器）
docker compose up -d

# 4. 运行测试
pytest tests/ -q
```

---

## 关键文档

| 文档 | 内容 |
|---|---|
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | 日常运维、健康检查、日志查看 |
| [docs/MIGRATION_RUNBOOK.md](docs/MIGRATION_RUNBOOK.md) | 数据迁移命令、输入输出、失败处理 |
| [docs/ROLLBACK_RUNBOOK.md](docs/ROLLBACK_RUNBOOK.md) | 回滚步骤、验证方法、恢复指引 |
| [docs/ACCEPTANCE_PROTOCOL.md](docs/ACCEPTANCE_PROTOCOL.md) | 自动/人工验收表格、阻断条件 |
| [docs/SECURITY.md](docs/SECURITY.md) | 端口、网络、Secret、认证、日志脱敏 |
| [docs/DATA_DICTIONARY.md](docs/DATA_DICTIONARY.md) | 数据库表、字段、约束、关系 |
| [docs/COMPATIBILITY_CONTRACT.md](docs/COMPATIBILITY_CONTRACT.md) | 适配层契约、XinChao↔Nocturne 协议映射 |

---

## 上游依赖

见 [THIRD_PARTY_MANIFEST.json](THIRD_PARTY_MANIFEST.json) 和 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

---

## 许可证

本仓库新增代码采用 MIT 许可证。上游组件保留其原始许可证。
