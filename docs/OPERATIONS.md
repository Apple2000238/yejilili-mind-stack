# OPERATIONS.md

## 日常操作

### 启动核心服务

```bash
docker compose up -d
```

这不会启动 migration-cli 和 acceptance-runner（它们有 `profiles`）。

### 运行迁移

```bash
# 1. 预检
./ops/preflight.sh --strict

# 2. 导出源数据
./ops/export-source.sh --source-db /path/to/source.db --run-id $(uuidgen) --read-only

# 3. 导入 staging
./ops/import-staging.sh --run-id <uuid>

# 4. 验证
./ops/verify-migration.sh --run-id <uuid>
```

### 运行验收测试

```bash
docker compose --profile acceptance up -d
docker compose logs -f acceptance-runner
```

### 查看服务状态

```bash
docker compose ps
docker compose logs -f <service-name>
```

## 维护操作

### 备份

```bash
# PostgreSQL 逻辑备份
docker compose exec continuity-ledger pg_dump -U continuity continuity_ledger > backup-$(date +%Y%m%d).sql
```

### 回滚

```bash
./ops/rollback-staging.sh --run-id <uuid>
```

## 故障排查

### 服务无法启动

1. 检查 preflight：`./ops/preflight.sh --strict`
2. 检查 secrets 目录和文件权限
3. 检查 upstream 源码是否已导入：`./ops/import-upstream.sh`
4. 检查 docker-compose config：`docker compose config`

### healthcheck 失败

1. 查看具体服务日志：`docker compose logs <service>`
2. 检查网络连通性：服务是否在正确的 Docker 网络中
3. 检查依赖服务是否 healthy
