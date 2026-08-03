# ROLLBACK_RUNBOOK.md

## 适用范围

本文档仅适用于 **staging/候选环境** 的回滚操作。生产环境回退遵循独立流程，需梨梨明确批准。

## 原则

1. **不删除源数据**：回滚只恢复目标 schema/volume snapshot，不删除原始导出或备份。
2. **保留证据**：失败的 run 保持冻结状态，错误日志、manifest 和 snapshot 全部保留。
3. **不接受生产路径**：`rollback-staging.sh` 拒绝任何包含 `/opt/afterrain-api` 或 `production` 的路径。

## 回滚步骤

### 逻辑回滚（默认）

```bash
# 1. 确认 run ID
./ops/rollback-staging.sh --run-id <uuid>

# 2. 验证回滚结果
./ops/verify-migration.sh --run-id <uuid>
```

逻辑回滚会：
- 在 `rollback_points` 表中记录回滚点
- 删除该 run 的所有投影数据（identity_projection, memory_projection, message_projection, summary_projection, promise_projection, affect_projection）
- 保留 `source_records` 作为审计证据
- 将 `migration_runs` 状态更新为 `rolled_back`

### Volume/Schema 回滚（高级）

如需恢复到迁移前的完整数据库状态：

```bash
# 停止相关服务
docker compose stop edge-gateway xinchao nocturne-adapter

# 恢复 PostgreSQL volume 快照（需提前创建）
# 具体命令取决于快照工具，如 restic/borg/zfs

# 验证 schema hash 与 pre snapshot 一致
docker compose exec continuity-ledger pg_dump -s continuity_ledger > /tmp/schema.sql
sha256sum /tmp/schema.sql
# 与 pre snapshot 中的 schema_hash 比较
```

## 回滚后验证

回滚后必须执行：
1. `verify-migration.sh` 确认该 run 状态为 `rolled_back`
2. 检查 projection 表中没有该 run 的数据
3. 确认 `rollback_points` 表中有对应的回滚记录
4. 确认源数据（source_records）仍然保留

## 失败处理

如果回滚脚本本身失败：
1. 不要手动删除数据
2. 记录完整的错误日志和 stack trace
3. 将 run 标记为 `rollback_failed`
4. 联系管理员手动处理
