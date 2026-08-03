-- 003_init_continuity_tables.sql
-- 连续性核心表：身份、驱动、心情、过渡、交接
-- 注：向量字段使用 JSONB 存储，如需 pgvector 扩展可在独立迁移中升级

-- ── pgvector 扩展（可选，如镜像支持）───────────────────────────────
DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS vector;
EXCEPTION WHEN undefined_file THEN
    -- pgvector 扩展不可用，向量字段将回退到 JSONB
END $$;

-- ── 身份连续性锚点 ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS identity_anchor (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    anchor_type TEXT NOT NULL,
        -- self_introduction | relationship | preference | boundary
    source_system TEXT NOT NULL,
        -- xinchao | nocturne | edge-gateway
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    vector_embedding JSONB,
        -- 存储为 JSONB 数组 [float, ...]；如需 pgvector 可后续 ALTER COLUMN 为 VECTOR(1536)
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_confirmed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    confidence REAL NOT NULL DEFAULT 1.0,
    metadata JSONB
);

CREATE INDEX IF NOT EXISTS idx_idanchor_type ON identity_anchor(anchor_type);
CREATE INDEX IF NOT EXISTS idx_idanchor_source ON identity_anchor(source_system);

-- ── 驱动状态快照（Desire Engine 状态）─────────────────────────────
CREATE TABLE IF NOT EXISTS drive_snapshot (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    snapshot_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_system TEXT NOT NULL,
    drives JSONB NOT NULL,
        -- { "drive_key": weight, ... }
    top_drive TEXT,
    intent JSONB,
    fatigue_level REAL,
    metadata JSONB
);

CREATE INDEX IF NOT EXISTS idx_drive_snap_time ON drive_snapshot(snapshot_at);
CREATE INDEX IF NOT EXISTS idx_drive_snap_source ON drive_snapshot(source_system);

-- ── 心情池归档（Mood Pool）────────────────────────────────────────
CREATE TABLE IF NOT EXISTS mood_archive (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    archived_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_system TEXT NOT NULL,
    current_mood TEXT,
    mood_vector JSONB,
    atmospheric_tags TEXT[],
    gravity_reading JSONB,
    metadata JSONB
);

CREATE INDEX IF NOT EXISTS idx_mood_time ON mood_archive(archived_at);

-- ── 过渡日志（Transition Journal）─────────────────────────────────
CREATE TABLE IF NOT EXISTS transition_journal (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    transition_type TEXT NOT NULL,
        -- session_start | session_end | migration | handoff | error
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    from_state TEXT,
    to_state TEXT,
    actor TEXT NOT NULL,
        -- xinchao | nocturne | edge-gateway | adapter
    context JSONB,
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_trans_type ON transition_journal(transition_type);
CREATE INDEX IF NOT EXISTS idx_trans_time ON transition_journal(occurred_at);

-- ── XinChao 交接记录（Handoff Notes）──────────────────────────────
CREATE TABLE IF NOT EXISTS xinchao_handoff (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    handoff_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    xinchao_session_id TEXT,
    thought_pool JSONB,
    context_envelope JSONB,
    ombre_state JSONB,
    bark_unread JSONB,
    handoff_note TEXT,
    processed BOOLEAN NOT NULL DEFAULT FALSE,
    processed_at TIMESTAMPTZ,
    metadata JSONB
);

CREATE INDEX IF NOT EXISTS idx_handoff_time ON xinchao_handoff(handoff_at);
CREATE INDEX IF NOT EXISTS idx_handoff_processed ON xinchao_handoff(processed);

-- ── Edge Gateway 配置表 ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS edge_gateway_config (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    config_key TEXT NOT NULL UNIQUE,
    config_value JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by TEXT
);

INSERT INTO edge_gateway_config (config_key, config_value)
VALUES ('llm_provider', '{"default": "mock", "available": ["openai", "anthropic", "mock"]}')
ON CONFLICT (config_key) DO NOTHING;
