# Migration Runbook — XinChao → Nocturne 连续性迁移操作手册

> **版本对齐**：本 Runbook 与 `services/migration-cli/src/main.py` argparse 子命令保持一致。
> 子命令清单：`snapshot-pre`、`snapshot-post`、`export-source`、`import-staging`、`verify`、`rollback`、`list-runs`。

## 1. 准备工作

### 1.1 环境要求
- Docker 24.0+ & Docker Compose 2.20+
- 至少 4GB 可用内存
- 目标 VPS 或本地 staging 环境

### 1.2 创建 Secret 文件
```bash
mkdir -p secrets
echo "your-mcp-adapter-token" > secrets/mcp_adapter_token.txt
echo "your-postgres-password" > secrets/postgres_password.txt
echo "your-openai-key" > secrets/openai_api_key.txt
echo "your-anthropic-key" > secrets/anthropic_api_key.txt
echo "your-service-token" > secrets/service_token.txt
echo "your-admin-token" > secrets/admin_token.txt
chmod 600 secrets/*
```

### 1.3 复制环境变量
```bash
cp .env.example .env
# 编辑 .env 填入实际值
```

## 2. 启动核心服务

```bash
docker compose up -d continuity-ledger nocturne nocturne-adapter edge-gateway xinchao
```

验证状态：
```bash
docker compose ps
docker compose logs -f nocturne-adapter
```

## 3. 迁移执行（标准六步工作流）

### 3.1 生成 Run ID
```bash
RUN_ID=$(uuidgen | tr '[:upper:]' '[:lower:]')
echo "Run ID: $RUN_ID"
```

### 3.2 Pre-Snapshot
记录运行前环境状态（Git commit、Compose hash、schema hash、磁盘余量）：
```bash
docker compose --profile migration run --rm migration-cli \
  python -m src.main snapshot-pre \
  --run-id "$RUN_ID" \
  --source-db /tmp/source.db
```

### 3.3 Export Source（从宿主机 SQLite）
使用 `ops/export-source.sh` 包装脚本，自动处理只读 bind mount：
```bash
./ops/export-source.sh \
  --source-db /absolute/path/to/afterrain.db \
  --run-id "$RUN_ID" \
  --read-only
```
> 注意：`--source-db` 必须是宿主机绝对路径。脚本会将该文件以只读方式挂载到容器内 `/tmp/source.db`。

### 3.4 Import Staging
将已导出的 source manifest 写入 PostgreSQL，生成六类连续性投影：
```bash
docker compose --profile migration run --rm migration-cli \
  python -m src.main import-staging \
  --run-id "$RUN_ID" \
  --mapping-version v1
```

### 3.5 Verify
校验 source manifest、source_records 行数、projection 完整性及 migration_runs 状态：
```bash
docker compose --profile migration run --rm migration-cli \
  python -m src.main verify \
  --run-id "$RUN_ID"
```
验证通过时退出码为 `0`，输出 `"overall": "PASS"`；任一检查失败时退出码非零。

### 3.6 Post-Snapshot（成功路径）
```bash
docker compose --profile migration run --rm migration-cli \
  python -m src.main snapshot-post \
  --run-id "$RUN_ID" \
  --exit-code 0
```

## 4. 回滚

### 4.1 按 Run ID 回滚
若验证失败或需要撤销本次迁移：
```bash
docker compose --profile migration run --rm migration-cli \
  python -m src.main rollback \
  --run-id "$RUN_ID"
```
回滚会：
- 创建 rollback point 记录；
- 删除该 `run_id` 对应的六张 projection 表数据；
- 保留 `source_records` 作为审计证据；
- 将 `migration_runs` 状态更新为 `rolled_back`；
- **不影响其他 run 的数据**。

### 4.2 查看迁移历史
```bash
docker compose --profile migration run --rm migration-cli \
  python -m src.main list-runs
```

### 4.3 紧急停止（仅停止容器，不自动回滚）
```bash
docker compose stop migration-cli
```
> 停止容器不会自动触发回滚；如需撤销，必须显式执行 `rollback --run-id`。

## 5. 验收检查

```bash
docker compose --profile acceptance run --rm acceptance-runner
```

## 6. 常见问题

| 问题 | 排查 |
|------|------|
| adapter 连接 nocturne 失败 | 检查 nocturne-private 网络，确认 nocturne 健康状态 |
| postgres 连接失败 | 检查 secrets/postgres_password.txt 是否存在且权限为 600 |
| provenance 记录重复 | 正常行为——adapter 幂等机制会自动标记 duplicate |
| edge-gateway 切换 provider 无效 | 确认目标 provider 的 API key secret 已正确挂载 |
| export-source 提示路径不存在 | 确认使用宿主机绝对路径，且文件可读 |
| verify 失败 "overall": "FAIL" | 检查 `source_records` 行数是否与 source manifest 一致；检查 `migration_runs.status` |

## 7. 生产 checklist

- [ ] 所有 secret 文件权限为 600
- [ ] .env 未提交到 Git
- [ ] postgres 密码 ≥ 32 位随机字符
- [ ] mcp_adapter_token 与 XinChao 配置一致
- [ ] service_token 与 XinChao / adapter 配置一致
- [ ] 上游 snapshot 版本与 THIRD_PARTY_MANIFEST 一致
- [ ] 迁移前已备份 XinChao state 目录
- [ ] 宿主机 source SQLite 已创建只读副本（禁止直接导出生产路径 `/opt/afterrain-api`）
