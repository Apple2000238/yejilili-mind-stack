-- 004_init_required_tables.sql
-- 补全连续性账本规格要求的全部核心表（适配规格 §5.2）
-- 本迁移在 001~003 之后执行，不重复创建已存在的 adapter_provenance/migration_batch

-- ── 迁移运行主表 ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS migration_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_name TEXT NOT NULL,
    git_commit TEXT,
    compose_config_hash TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'pending',
        -- pending | running | completed | failed | rolled_back
    source_snapshot_hash TEXT NOT NULL,
    mapping_version TEXT NOT NULL DEFAULT 'v1',
    records_total INTEGER,
    records_migrated INTEGER DEFAULT 0,
    records_failed INTEGER DEFAULT 0,
    pre_snapshot_path TEXT,
    post_snapshot_path TEXT,
    manifest_path TEXT,
    error_log TEXT,
    metadata JSONB
);
CREATE INDEX IF NOT EXISTS idx_mig_runs_status ON migration_runs(status);
CREATE INDEX IF NOT EXISTS idx_mig_runs_snapshot_hash ON migration_runs(source_snapshot_hash);
CREATE INDEX IF NOT EXISTS idx_mig_runs_started ON migration_runs(started_at);

-- ── 源快照记录 ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS source_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES migration_runs(id) ON DELETE CASCADE,
    snapshot_type TEXT NOT NULL,
        -- pre | post
    snapshot_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    db_schema_hash TEXT NOT NULL,
    db_row_count INTEGER,
    db_merkle_root TEXT,
    disk_free_bytes BIGINT,
    snapshot_path TEXT,
    snapshot_sha256 TEXT,
    metadata JSONB
);
CREATE INDEX IF NOT EXISTS idx_src_snap_run ON source_snapshots(run_id);
CREATE INDEX IF NOT EXISTS idx_src_snap_type ON source_snapshots(snapshot_type);

-- ── 源表清单 ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS source_table_manifest (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES migration_runs(id) ON DELETE CASCADE,
    source_table TEXT NOT NULL,
    schema_hash TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    merkle_root TEXT,
    primary_key_strategy TEXT,
    excluded_secret_fields TEXT[],
    time_range_min TIMESTAMPTZ,
    time_range_max TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_stm_run ON source_table_manifest(run_id);
CREATE INDEX IF NOT EXISTS idx_stm_table ON source_table_manifest(source_table);

-- ── 源记录原文（去密后规范 JSON）───────────────────────────────────
CREATE TABLE IF NOT EXISTS source_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES migration_runs(id) ON DELETE CASCADE,
    source_table TEXT NOT NULL,
    source_pk TEXT NOT NULL,
    payload_json JSONB NOT NULL,
    payload_hash TEXT NOT NULL,
    canonical_hash TEXT NOT NULL,
    mapping_version TEXT NOT NULL DEFAULT 'v1',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(run_id, source_table, source_pk)
);
CREATE INDEX IF NOT EXISTS idx_sr_run ON source_records(run_id);
CREATE INDEX IF NOT EXISTS idx_sr_table ON source_records(source_table);
CREATE INDEX IF NOT EXISTS idx_sr_hash ON source_records(canonical_hash);

-- ── 身份投影 ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS identity_projection (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES migration_runs(id) ON DELETE CASCADE,
    source_table TEXT NOT NULL,
    source_pk TEXT NOT NULL,
    source_content_hash TEXT NOT NULL,
    mapping_version TEXT NOT NULL DEFAULT 'v1',
    persona_json JSONB,
    protected_layers JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(run_id, source_table, source_pk)
);
CREATE INDEX IF NOT EXISTS idx_ip_run ON identity_projection(run_id);
CREATE INDEX IF NOT EXISTS idx_ip_source ON identity_projection(source_table, source_pk);

-- ── 记忆投影 ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS memory_projection (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES migration_runs(id) ON DELETE CASCADE,
    source_table TEXT NOT NULL,
    source_pk TEXT NOT NULL,
    source_content_hash TEXT NOT NULL,
    mapping_version TEXT NOT NULL DEFAULT 'v1',
    bucket_yaml TEXT,
    nocturne_ref TEXT,
    layer_type TEXT,
    protected BOOLEAN NOT NULL DEFAULT FALSE,
    embedding_status TEXT DEFAULT 'pending',
        -- pending | done | retryable_failed
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(run_id, source_table, source_pk)
);
CREATE INDEX IF NOT EXISTS idx_mp_run ON memory_projection(run_id);
CREATE INDEX IF NOT EXISTS idx_mp_source ON memory_projection(source_table, source_pk);
CREATE INDEX IF NOT EXISTS idx_mp_ref ON memory_projection(nocturne_ref);

-- ── 消息投影 ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS message_projection (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES migration_runs(id) ON DELETE CASCADE,
    source_table TEXT NOT NULL,
    source_pk TEXT NOT NULL,
    source_content_hash TEXT NOT NULL,
    mapping_version TEXT NOT NULL DEFAULT 'v1',
    role TEXT NOT NULL,
    session_id TEXT,
    room TEXT,
    platform TEXT,
    content TEXT,
    archived BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(run_id, source_table, source_pk)
);
CREATE INDEX IF NOT EXISTS idx_msgp_run ON message_projection(run_id);
CREATE INDEX IF NOT EXISTS idx_msgp_session ON message_projection(session_id);
CREATE INDEX IF NOT EXISTS idx_msgp_role ON message_projection(role);

-- ── 摘要投影 ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS summary_projection (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES migration_runs(id) ON DELETE CASCADE,
    source_table TEXT NOT NULL,
    source_pk TEXT NOT NULL,
    source_content_hash TEXT NOT NULL,
    mapping_version TEXT NOT NULL DEFAULT 'v1',
    summary_type TEXT NOT NULL,
        -- daily | hourly | weekly | monthly | yearly | digest
    summary_text TEXT,
    batch_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(run_id, source_table, source_pk)
);
CREATE INDEX IF NOT EXISTS idx_sp_run ON summary_projection(run_id);
CREATE INDEX IF NOT EXISTS idx_sp_type ON summary_projection(summary_type);

-- ── 约定投影 ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS promise_projection (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES migration_runs(id) ON DELETE CASCADE,
    source_table TEXT NOT NULL,
    source_pk TEXT NOT NULL,
    source_content_hash TEXT NOT NULL,
    mapping_version TEXT NOT NULL DEFAULT 'v1',
    promise_text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
        -- active | fulfilled | broken | cancelled
    due_date TIMESTAMPTZ,
    fulfilled_at TIMESTAMPTZ,
    emotion_weight REAL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(run_id, source_table, source_pk)
);
CREATE INDEX IF NOT EXISTS idx_pp_run ON promise_projection(run_id);
CREATE INDEX IF NOT EXISTS idx_pp_status ON promise_projection(status);

-- ── 情感/状态投影 ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS affect_projection (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES migration_runs(id) ON DELETE CASCADE,
    source_table TEXT NOT NULL,
    source_pk TEXT NOT NULL,
    source_content_hash TEXT NOT NULL,
    mapping_version TEXT NOT NULL DEFAULT 'v1',
    affect_type TEXT NOT NULL,
        -- dream | whisper | state | diary | knot | proactive
    content TEXT,
    pinned BOOLEAN NOT NULL DEFAULT FALSE,
    deleted BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(run_id, source_table, source_pk)
);
CREATE INDEX IF NOT EXISTS idx_ap_run ON affect_projection(run_id);
CREATE INDEX IF NOT EXISTS idx_ap_type ON affect_projection(affect_type);

-- ── 会话表 ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS conversation_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id TEXT NOT NULL UNIQUE,
    namespace TEXT NOT NULL DEFAULT 'default',
    platform TEXT,
    room TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_active_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata JSONB
);
CREATE INDEX IF NOT EXISTS idx_cs_session ON conversation_sessions(session_id);
CREATE INDEX IF NOT EXISTS idx_cs_namespace ON conversation_sessions(namespace);

-- ── 会话消息表 ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS conversation_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id TEXT NOT NULL REFERENCES conversation_sessions(session_id) ON DELETE CASCADE,
    message_index INTEGER NOT NULL,
    role TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    token_count INTEGER,
    source_refs JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(session_id, message_index)
);
CREATE INDEX IF NOT EXISTS idx_cm_session ON conversation_messages(session_id);
CREATE INDEX IF NOT EXISTS idx_cm_role ON conversation_messages(role);

-- ── 心智事件表 ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS mind_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id TEXT NOT NULL UNIQUE,
    session_hash TEXT NOT NULL,
    interaction_type TEXT NOT NULL,
        -- companionship | affection | intimacy | sharing | discovery
        -- task_progress | reflection | conflict | loss | reconciliation
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_version TEXT,
    metadata JSONB
);
CREATE INDEX IF NOT EXISTS idx_me_event ON mind_events(event_id);
CREATE INDEX IF NOT EXISTS idx_me_type ON mind_events(interaction_type);
CREATE INDEX IF NOT EXISTS idx_me_time ON mind_events(occurred_at);

-- ── 检索审计（不保存完整正文）─────────────────────────────────────
CREATE TABLE IF NOT EXISTS retrieval_audit (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id_hash TEXT NOT NULL,
    session_id_hash TEXT,
    route TEXT NOT NULL,
    source_refs JSONB,
    token_budget INTEGER,
    truncation_count INTEGER,
    latency_ms INTEGER,
    error_class TEXT,
    side_effect_class TEXT,
        -- state_tick | mood_decoration | dream_refresh
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ra_req ON retrieval_audit(request_id_hash);
CREATE INDEX IF NOT EXISTS idx_ra_time ON retrieval_audit(created_at);

-- ── 验收用例表 ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS acceptance_cases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id TEXT NOT NULL UNIQUE,
    case_name TEXT NOT NULL,
    category TEXT NOT NULL,
        -- identity | relationship | memory | promise | protocol | security | rollback
    description TEXT NOT NULL,
    criticality TEXT NOT NULL DEFAULT 'P1',
        -- P0 | P1 | P2
    expected_result TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── 验收结果表 ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS acceptance_results (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL,
    case_id TEXT NOT NULL REFERENCES acceptance_cases(case_id),
    passed BOOLEAN NOT NULL,
    actual_result TEXT,
    diff TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ar_run ON acceptance_results(run_id);
CREATE INDEX IF NOT EXISTS idx_ar_case ON acceptance_results(case_id);

-- ── 回滚点表 ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS rollback_points (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES migration_runs(id) ON DELETE CASCADE,
    point_name TEXT NOT NULL,
    point_type TEXT NOT NULL DEFAULT 'logical',
        -- logical | volume | schema
    snapshot_path TEXT,
    snapshot_hash TEXT,
    schema_version TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    restored_at TIMESTAMPTZ,
    restore_success BOOLEAN
);
CREATE INDEX IF NOT EXISTS idx_rp_run ON rollback_points(run_id);
CREATE INDEX IF NOT EXISTS idx_rp_created ON rollback_points(created_at);
