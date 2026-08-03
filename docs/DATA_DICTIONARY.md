# DATA_DICTIONARY.md

## 连续性账本数据字典

### migration_runs
迁移运行主表，记录每次迁移的元数据和状态。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID PK | 迁移运行唯一标识 |
| run_name | TEXT | 运行名称 |
| git_commit | TEXT | 运行时的 Git commit |
| compose_config_hash | TEXT | docker-compose.yml 的 SHA256 |
| source_snapshot_hash | TEXT | 源快照哈希 |
| mapping_version | TEXT | 映射版本（默认 v1） |
| status | TEXT | pending/running/completed/failed/rolled_back |
| records_total | INTEGER | 总记录数 |
| records_migrated | INTEGER | 已迁移记录数 |

### source_records
去密后的源记录原文，规范 JSON 格式。

| 字段 | 类型 | 说明 |
|------|------|------|
| run_id | UUID FK | 所属迁移运行 |
| source_table | TEXT | 源表名 |
| source_pk | TEXT | 源表主键 |
| payload_json | JSONB | 去密后的规范 JSON |
| payload_hash | TEXT | payload 的 SHA256 |
| canonical_hash | TEXT | 规范哈希（用于幂等比较） |

### identity_projection / memory_projection / message_projection / summary_projection / promise_projection / affect_projection
规范化投影表，分别对应身份、记忆、消息、摘要、约定、情感数据。

共性字段：
- `run_id`, `source_table`, `source_pk`, `source_content_hash`, `mapping_version`
- `created_at`, `updated_at`

约束：`(run_id, source_table, source_pk)` 唯一

### adapter_provenance
MCP 调用审计记录。

| 字段 | 类型 | 说明 |
|------|------|------|
| event_id | TEXT | 事件标识 |
| tool_name | TEXT | 工具名（breath/hold） |
| caller_subject | TEXT | 调用方身份 |
| auto | BOOLEAN | 是否为自动写入 |
| source | TEXT | 来源标识 |
| input_hash | TEXT | 输入参数哈希 |
| target_ref | TEXT | Nocturne 目标引用 |
| idempotency_status | TEXT | new/duplicate/retryable_failed/terminal |

唯一约束：`(event_id, tool_name, input_hash)` 用于并发幂等

### retrieval_audit
检索审计，不保存完整正文。

| 字段 | 类型 | 说明 |
|------|------|------|
| request_id_hash | TEXT | 请求 ID 的哈希 |
| session_id_hash | TEXT | 会话 ID 的哈希 |
| route | TEXT | 检索路由 |
| token_budget | INTEGER | token 预算 |
| latency_ms | INTEGER | 时延（毫秒） |
| error_class | TEXT | 错误分类 |
| side_effect_class | TEXT | state_tick/mood_decoration/dream_refresh |
