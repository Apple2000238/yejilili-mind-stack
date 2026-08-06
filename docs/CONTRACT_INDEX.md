# 契约版本与 Schema 索引

> 文档版本：1.0.0  
> 对应架构决定：保留现有 AR 生产基座，候选仓库为旁路侧车  
> 生效基线 commit：3d39b81dbcadae551dbb4b3af19eeb5e69fe2aa0  

---

## 1. 身份装配契约（Identity Gate）

**唯一来源**：`services/continuity-guard/src/identity_gate.py`

**五段顺序（固定，不可变）**：

| 优先级 | 段名称 | 是否可截断 | 缺失行为 |
|---|---|---|---|
| 1 | `core_instruction` | 否 | readiness 失败 |
| 2 | `identity_bedrock` | 否 | readiness 失败 |
| 3 | `long_term_memory` | 是（先压缩） | warning，允许 omission_reason 降级 |
| 4 | `recent_continuity` | 是（后压缩） | warning，允许 omission_reason 降级 |
| 5 | `session_messages` | 否（最低需要保留） | 硬预算不足时 overflow 失败 |

**版本**：`schema_version: "1.0.0"`（`identity_gate.json.example`）

**非目标**：`edge-gateway/src/prompt_plan.py` 的三段装配（identity_bedrock / continuity_context / system_instruction）**不是**身份装配契约，R6-02 中统一改造为消费结构化 PromptPlan。

---

## 2. 目标服务契约

### 2.1 Nocturne

| 项 | 值 |
|---|---|
| 入口 | `POST /mcp` |
| 协议 | JSON-RPC 2.0 |
| 允许工具 | `breath`、`hold` |
| 不允许 | `/api/v1/hold`（不存在） |
| receipt 字段 | `jsonrpc`、`id`、`result.content`、`result.metadata` |

**错误码**：
- `-32601` Unknown method/tool
- `-32602` Invalid params（含未知字段）
- `-32603` Internal error

### 2.2 心潮

| 事件类型 | HTTP 路由 | 方法 |
|---|---|---|
| conversation / heartbeat | `POST /v1/conversation-event` | 同步事件 |
| drive feedback | `POST /v1/drive-feedback` | 驱动反馈 |
| handoff note | `POST /v1/handoff-note` | 交接便签 |
| context envelope | `GET /v1/context` | 上下文查询 |
| state query | `GET /v1/state` | 状态只读 |
| breath context | `GET /v1/breath-context` | 呼吸上下文 |
| intent | `GET /v1/intent` | 意图查询 |
| settle cycle | `POST /v1/settle` | 周期结算 |

**不存在路由**（第六轮必须移除/封存）：
- `POST /api/continuity/ingest`
- `POST /api/drive/apply`
- `POST /api/event`

---

## 3. 事件信封契约

**版本**：`EVENT_SCHEMA_VERSION = "1.0.0"`

**必填字段**：
```text
event_id          — 全局唯一
correlation_id    — 默认同 event_id
causation_id      — 父事件 ID（空串表示根事件）
origin            — 白名单：nocturne, xinchao, edge-gateway, migration-cli, continuity-guard
event_type        — 按方向白名单
occurred_at       — ISO 8601
received_at       — ISO 8601
namespace
derived_from     — 结构化祖先链
payload_hash      — SHA256(payload_canonical_json)
payload           — 业务负载
```

**payload_hash 校验**：`hmac.compare_digest` 防时序攻击。

---

## 4. 事件状态机契约

**版本**：1.0.0

**状态及允许转移**：

```
pending ──claim──> claimed ──start──> processing ──success──> completed
    │                  │                   │
    │                  │                   └──failure──> failed ──retry──> pending
    │                  │
    │                  └──lease_timeout──> pending（崩溃恢复）
    │
    └──duplicate/completed_with_receipt──> skip
```

**关键规则**：
- 首次插入：status='pending', attempt=0
- claim：原子 UPDATE … WHERE status='pending'，带 `claimed_at`、`claimed_by`、`lease_expires_at`
- 超时恢复：lease_expires_at < now() 时，任何真实入口或 sweeper 可重新 claim
- 同一 event_id + 同一 payload_hash：幂等跳过
- 同一 event_id + 不同 payload_hash：冲突失败，记录 `event_idempotency_conflicts`
- completed 必须有可验证 receipt

---

## 5. 投影契约

| 投影表 | 来源事件 | 唯一键 | 可重建 |
|---|---|---|---|
| `identity_projection` | persona / memory_layers | (run_id, source_table, source_pk) | ✅ |
| `memory_projection` | ar_buckets | (run_id, source_table, source_pk) | ✅ |
| `message_projection` | message_archive / message_buffer / chat_sessions | (run_id, source_table, source_pk) | ✅ |
| `summary_projection` | daily_summaries / weekly_summaries | (run_id, source_table, source_pk) | ✅ |
| `promise_projection` | promises | (run_id, source_table, source_pk) | ✅ |
| `affect_projection` | ar_dreams / ar_whispers / diary / knots / ar_state / proactive_messages / room_visits | (run_id, source_table, source_pk) | ✅ |

**数据主权**：全部为可重建派生数据；SQLite 为生产事实真源。

---

## 6. 回滚契约

**完整回滚定义**：
- buckets 正文 + frontmatter 恢复到快照等价
- ledger 表恢复到快照行数/内容等价
- state 文件恢复到快照等价
- 迁移期间新增/删除/修改文件全部反转
- 使用临时目录 + 校验后原子替换
- 任一步失败 → run 明确失败，不标 `rolled_back`

**有限恢复**：若技术上只能做到部分恢复，接口/文档必须明确称"有限恢复"，不得称"完整回滚"。

---

## 7. 控制面状态契约

| 状态 | 生产响应 | 侧车写入 | 用途 |
|---|---|---|---|
| `off` | 旧 AR | 无 | 默认与紧急回退 |
| `shadow` | 旧 AR | 仅隔离审计/差异数据 | 观察，不改变用户结果 |
| `assist-canary` | 旧 AR + 经校验侧车上下文 | 受控 outbox | 指定测试会话验证 |
| `assist` | 旧 AR + 经校验侧车能力 | 受控 outbox | 侧车增强 |
| `replace:<capability>` | 只替换指定能力 | 按单写者契约 | 单项能力已独立验收后替换 |
| 未知 | 旧 AR | 无 | fail-closed 到 off |

---

## 8. 全仓旧语义命中清单

| 位置 | 旧表述/实现 | 状态 | 处理计划 |
|---|---|---|---|
| `README.md:5` | "候选替代环境" | ❌ 待修 | R6-00：改为"旁路侧车" |
| `services/continuity-guard/src/main.py:140` | `POST /api/v1/hold` | ❌ 待修 | R6-04：改为 `POST /mcp` |
| `services/continuity-guard/src/main.py:163` | `POST /api/continuity/ingest` | ❌ 待修 | R6-05：改为 `POST /v1/conversation-event` |
| `services/continuity-guard/src/main.py:165` | `POST /api/drive/apply` | ❌ 待修 | R6-05：改为 `POST /v1/drive-feedback` |
| `services/continuity-guard/src/main.py:167` | `POST /api/event` | ❌ 待修 | R6-05：按事件类型分发 |
| `services/edge-gateway/src/prompt_plan.py` | 三段装配（identity_bedrock / continuity_context / system_instruction） | ❌ 待修 | R6-02：改造为消费结构化 PromptPlan |
| `services/migration-cli/src/main.py` | `rollback_run()` 部分恢复+ledger告警 | ⚠️ 待补全 | R6-08：完整快照等价恢复 |
| `event_bridge.py:insert_or_check()` | claimed/processing 返回 skip | ❌ 待修 | R6-06：重构为可重放入口 |
