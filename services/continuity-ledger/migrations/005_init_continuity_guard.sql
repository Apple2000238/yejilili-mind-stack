-- Migration 005: continuity_guard 支撑表
-- 为第四轮架构要求提供数据库支撑

-- 事件幂等性日志
CREATE TABLE IF NOT EXISTS event_idempotency_log (
    event_id            TEXT PRIMARY KEY,
    payload_hash        TEXT NOT NULL,
    origin              TEXT NOT NULL,
    event_type          TEXT NOT NULL,
    first_seen_at       TIMESTAMPTZ DEFAULT now(),
    completed_at        TIMESTAMPTZ,
    status              TEXT NOT NULL DEFAULT 'recorded',
    error               TEXT,
    CONSTRAINT status_check CHECK (status IN ('recorded', 'completed', 'failed'))
);

CREATE INDEX IF NOT EXISTS idx_event_idempotency_status ON event_idempotency_log(status);
CREATE INDEX IF NOT EXISTS idx_event_idempotency_origin ON event_idempotency_log(origin);

-- 事件冲突记录（同一 event_id 不同 payload）
CREATE TABLE IF NOT EXISTS event_idempotency_conflicts (
    event_id                TEXT PRIMARY KEY,
    existing_payload_hash   TEXT NOT NULL,
    new_payload_hash        TEXT NOT NULL,
    detected_at             TIMESTAMPTZ DEFAULT now(),
    resolved_at             TIMESTAMPTZ,
    resolution              TEXT
);

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
