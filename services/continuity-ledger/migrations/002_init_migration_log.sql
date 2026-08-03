-- 002_init_migration_log.sql
-- 迁移日志：记录从 XinChao → Nocturne 的每一次数据迁移批次

CREATE TABLE IF NOT EXISTS migration_batch (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_name TEXT NOT NULL,
    source_system TEXT NOT NULL DEFAULT 'xinchao',
    target_system TEXT NOT NULL DEFAULT 'nocturne',
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'pending',
        -- pending | running | completed | failed | rolled_back
    records_total INTEGER,
    records_migrated INTEGER DEFAULT 0,
    records_failed INTEGER DEFAULT 0,
    checkpoint JSONB,
    error_log TEXT,
    metadata JSONB
);

CREATE INDEX IF NOT EXISTS idx_mig_status ON migration_batch(status);
CREATE INDEX IF NOT EXISTS idx_mig_started ON migration_batch(started_at);

-- 单条记录迁移明细
CREATE TABLE IF NOT EXISTS migration_record (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_id UUID NOT NULL REFERENCES migration_batch(id) ON DELETE CASCADE,
    source_id TEXT NOT NULL,
    target_id TEXT,
    record_type TEXT NOT NULL,
        -- memory | state | handoff | thought | dream | heartbeat
    status TEXT NOT NULL DEFAULT 'pending',
        -- pending | migrated | failed | skipped
    source_payload JSONB,
    transformed_payload JSONB,
    error TEXT,
    migrated_at TIMESTAMPTZ,
    metadata JSONB
);

CREATE INDEX IF NOT EXISTS idx_migrec_batch ON migration_record(batch_id);
CREATE INDEX IF NOT EXISTS idx_migrec_source ON migration_record(source_id);
CREATE INDEX IF NOT EXISTS idx_migrec_status ON migration_record(status);
