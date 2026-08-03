# Migration Runbook — XinChao → Nocturne 连续性迁移操作手册

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
chmod 600 secrets/*
```

### 1.3 复制环境变量
cp .env.example .env
# 编辑 .env 填入实际值

## 2. 启动核心服务

```bash
docker compose up -d continuity-ledger nocturne nocturne-adapter edge-gateway
```

验证状态：
```bash
docker compose ps
docker compose logs -f nocturne-adapter
```

## 3. 迁移执行

### 3.1 单条迁移（测试用）
```bash
docker compose --profile migration run --rm migration-cli \
  python -m src.main migrate-one \
  --source-id "xinchao-memory-001" \
  --type memory
```

### 3.2 批量迁移
```bash
docker compose --profile migration run --rm migration-cli \
  python -m src.main migrate-batch \
  --source xinchao \
  --target nocturne \
  --checkpoint /artifacts/checkpoint.json
```

### 3.3 查看迁移进度
```bash
docker compose exec continuity-ledger psql -U continuity -d continuity_ledger -c "
  SELECT status, COUNT(*) FROM migration_batch GROUP BY status;
"
```

## 4. 回滚

### 4.1 单批次回滚
```bash
docker compose --profile migration run --rm migration-cli \
  python -m src.main rollback \
  --batch-id <batch-uuid>
```

### 4.2 紧急停止
```bash
docker compose stop migration-cli
docker compose exec continuity-ledger psql -U continuity -d continuity_ledger -c "
  UPDATE migration_batch SET status = 'rolled_back' WHERE status = 'running';
"
```

## 5. 验收检查

```bash
docker compose --profile acceptance run --rm acceptance-runner
```

## 6. 常见问题

| 问题 | 排查 |
|------|------|
| adapter 连接 nocturne 失败 | 检查 nocturne-private 网络，确认 nocturne 健康状态 |
| postgres 连接失败 | 检查 secrets/postgres_password.txt 是否存在 |
| provenance 记录重复 | 正常行为——adapter 幂等机制会自动标记 duplicate |
| edge-gateway 切换 provider 无效 | 确认目标 provider 的 API key secret 已正确挂载 |

## 7. 生产 checklist

- [ ] 所有 secret 文件权限为 600
- [ ] .env 未提交到 Git
- [ ] postgres 密码 ≥ 32 位随机字符
- [ ] mcp_adapter_token 与 XinChao 配置一致
- [ ] 上游 snapshot 版本与 THIRD_PARTY_MANIFEST 一致
- [ ] 迁移前已备份 XinChao state 目录
