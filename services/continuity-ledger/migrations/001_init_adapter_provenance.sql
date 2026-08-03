-- 001_init_adapter_provenance.sql
-- Adapter provenance 账本：记录每一次 MCP 调用的来源、路由与结果

CREATE TABLE IF NOT EXISTS adapter_provenance (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    caller_subject TEXT NOT NULL,
    auto BOOLEAN,
    source TEXT,
    input_hash TEXT NOT NULL,
    target_kind TEXT NOT NULL,
    target_ref TEXT,
    result_hash TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    idempotency_status TEXT NOT NULL DEFAULT 'new',
    error TEXT,
    metadata JSONB
);

CREATE INDEX IF NOT EXISTS idx_prov_event_id ON adapter_provenance(event_id);
CREATE INDEX IF NOT EXISTS idx_prov_tool ON adapter_provenance(tool_name);
CREATE INDEX IF NOT EXISTS idx_prov_caller ON adapter_provenance(caller_subject);
CREATE INDEX IF NOT EXISTS idx_prov_created ON adapter_provenance(created_at);

-- 为幂等性建立唯一约束（event_id + tool_name + input_hash）
CREATE UNIQUE INDEX IF NOT EXISTS idx_prov_idempotent
ON adapter_provenance(event_id, tool_name, input_hash)
WHERE idempotency_status IN ('new', 'duplicate');
