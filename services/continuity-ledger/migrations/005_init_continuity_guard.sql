-- Migration 005: continuity_guard 支撑表
-- 为第四轮架构要求提供数据库支撑
-- 更新：添加 inbox/outbox 状态机、causation 链、overflow 字段

-- 事件 inbox/outbox 状态机（P0-07）
CREATE TABLE IF NOT EXISTS event_inbox (
    event_id            TEXT PRIMARY KEY,
    correlation_id      TEXT NOT NULL,
    causation_id        TEXT,
    origin              TEXT NOT NULL,
    event_type          TEXT NOT NULL,
    namespace           TEXT,
    derived_from        TEXT,
    payload_hash        TEXT NOT NULL,
    payload             JSONB,
    status              TEXT NOT NULL DEFAULT 'pending',
    attempt             INTEGER NOT NULL DEFAULT 0,
    receipt             JSONB,
    error               TEXT,
    created_at          TIMESTAMPTZ DEFAULT now(),
    claimed_at          TIMESTAMPTZ,
    claimed_by          TEXT,
    completed_at        TIMESTAMPTZ,
    updated_at          TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT status_check CHECK (status IN ('pending', 'claimed', 'processing', 'completed', 'failed'))
);

CREATE INDEX IF NOT EXISTS idx_event_inbox_status ON event_inbox(status);
CREATE INDEX IF NOT EXISTS idx_event_inbox_origin ON event_inbox(origin);
CREATE INDEX IF NOT EXISTS idx_event_inbox_correlation ON event_inbox(correlation_id);
CREATE INDEX IF NOT EXISTS idx_event_inbox_updated ON event_inbox(updated_at);

-- 事件冲突记录（同一 event_id 不同 payload）
CREATE TABLE IF NOT EXISTS event_idempotency_conflicts (
    event_id                TEXT PRIMARY KEY,
    existing_payload_hash   TEXT NOT NULL,
    new_payload_hash        TEXT NOT NULL,
    detected_at             TIMESTAMPTZ DEFAULT now(),
    resolved_at             TIMESTAMPTZ,
    resolution              TEXT
);

-- 事件 causation 链（P0-08：持久化回环检测）
CREATE TABLE IF NOT EXISTS event_causation_chain (
    event_id        TEXT PRIMARY KEY,
    correlation_id  TEXT NOT NULL,
    causation_id    TEXT,
    origin          TEXT NOT NULL,
    event_type      TEXT NOT NULL,
    derived_from    TEXT,
    hop_count       INTEGER NOT NULL DEFAULT 0,
    recorded_at     TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_causation_correlation ON event_causation_chain(correlation_id);
CREATE INDEX IF NOT EXISTS idx_causation_event ON event_causation_chain(event_id);

-- 同步审计日志
CREATE TABLE IF NOT EXISTS manifest_sync_audit (
    id                  SERIAL PRIMARY KEY,
    sync_id             TEXT NOT NULL UNIQUE,
    manifest_id         TEXT NOT NULL,
    bucket_id           TEXT NOT NULL,
    operation           TEXT NOT NULL,
    content_hash_before TEXT,
    content_hash_after  TEXT,
    metadata_before     JSONB,
    metadata_after      JSONB,
    manifest_entry      JSONB,
    started_at          TIMESTAMPTZ,
    completed_at        TIMESTAMPTZ,
    success             BOOLEAN NOT NULL DEFAULT false,
    error               TEXT
);

CREATE INDEX IF NOT EXISTS idx_sync_audit_bucket ON manifest_sync_audit(bucket_id);
CREATE INDEX IF NOT EXISTS idx_sync_audit_manifest ON manifest_sync_audit(manifest_id);

-- 身份装配审计日志
CREATE TABLE IF NOT EXISTS identity_assembly_audit (
    id                      SERIAL PRIMARY KEY,
    assembly_id             TEXT NOT NULL UNIQUE,
    sections                JSONB,
    total_tokens            INTEGER,
    token_budget            INTEGER,
    identity_bedrock_present BOOLEAN NOT NULL DEFAULT false,
    identity_bedrock_hash   TEXT,
    truncated               BOOLEAN NOT NULL DEFAULT false,
    overflow                BOOLEAN NOT NULL DEFAULT false,
    created_at              TIMESTAMPTZ DEFAULT now()
);

-- Dashboard 访问日志（审计）
CREATE TABLE IF NOT EXISTS dashboard_access_log (
    id          SERIAL PRIMARY KEY,
    accessed_at TIMESTAMPTZ DEFAULT now(),
    endpoint    TEXT NOT NULL,
    client_ip   TEXT,
    token_hash  TEXT,  -- 只存 hash，不存明文 token
    success     BOOLEAN NOT NULL DEFAULT false
);

CREATE INDEX IF NOT EXISTS idx_dashboard_access_at ON dashboard_access_log(accessed_at);

-- Dashboard 支撑表：Breath 结果摘要
CREATE TABLE IF NOT EXISTS breath_results (
    id              SERIAL PRIMARY KEY,
    source_refs     TEXT[] NOT NULL DEFAULT '{}',
    summary         TEXT NOT NULL,
    relevance_score FLOAT NOT NULL DEFAULT 0.0,
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- Dashboard 支撑表：心潮十二维快照
CREATE TABLE IF NOT EXISTS dimension_snapshots (
    id          SERIAL PRIMARY KEY,
    dimension   TEXT NOT NULL,
    value       FLOAT NOT NULL DEFAULT 0.0,
    delta_1h    FLOAT NOT NULL DEFAULT 0.0,
    checked_at  TIMESTAMPTZ DEFAULT now(),
    UNIQUE (dimension, checked_at)
);

CREATE INDEX IF NOT EXISTS idx_dimension_checked ON dimension_snapshots(dimension, checked_at);

-- Dashboard 支撑表：念头元数据
CREATE TABLE IF NOT EXISTS thought_meta (
    id          SERIAL PRIMARY KEY,
    thought_id  TEXT NOT NULL UNIQUE,
    created_at  TIMESTAMPTZ DEFAULT now(),
    emotion_tag TEXT,
    intensity   FLOAT NOT NULL DEFAULT 0.0
);
