-- M5 migration 005: LangGraph AI agent infrastructure.
-- Sections: unaccent FTS index -> admin_audit -> agent_daily_usage -> checkpointer tables -> agent_readonly role.

-- 1. unaccent expression GIN index for FTS over product_chunks.
-- The existing `search_vector` column is generated from `to_tsvector('simple', content)`;
-- we add a separate expression index that includes unaccent() so Vietnamese diacritics match.
--
-- Postgres requires the expression to be IMMUTABLE. The bundled `unaccent()`
-- function is STABLE (depends on the search dictionary), so we wrap it in
-- an IMMUTABLE shim that ignores the regdictionary argument.
CREATE EXTENSION IF NOT EXISTS unaccent;

CREATE OR REPLACE FUNCTION public.immutable_unaccent(text)
RETURNS text
LANGUAGE sql
IMMUTABLE
STRICT
PARALLEL SAFE
AS $$
    SELECT public.unaccent('public.unaccent', $1);
$$;

CREATE INDEX IF NOT EXISTS idx_product_chunks_fts_unaccent
    ON product_chunks
    USING GIN (to_tsvector('simple', public.immutable_unaccent(coalesce(content, ''))));

-- 2. admin_audit: who/what/when for admin-only tool calls.
CREATE TABLE IF NOT EXISTS admin_audit (
    id BIGSERIAL PRIMARY KEY,
    user_id_hash TEXT NOT NULL,
    action TEXT NOT NULL,
    target_id TEXT,
    response_size_bytes INT,
    trace_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_admin_audit_user_time
    ON admin_audit (user_id_hash, created_at DESC);

-- 3. agent_daily_usage: one row per day; atomic increment for daily budget counter.
CREATE TABLE IF NOT EXISTS agent_daily_usage (
    usage_date DATE PRIMARY KEY,
    tokens_used BIGINT NOT NULL DEFAULT 0,
    requests_count BIGINT NOT NULL DEFAULT 0,
    last_increment_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 4. LangGraph checkpointer tables.
-- These three tables are also created automatically by AsyncPostgresSaver.setup().
-- We pre-create them here so deployments don't need a second code path.
CREATE TABLE IF NOT EXISTS checkpoints (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    parent_checkpoint_id TEXT,
    type TEXT,
    checkpoint JSONB NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
);
CREATE INDEX IF NOT EXISTS idx_checkpoints_thread_id ON checkpoints (thread_id);

CREATE TABLE IF NOT EXISTS checkpoint_writes (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    channel TEXT NOT NULL,
    type TEXT,
    blob BYTEA NOT NULL,
    task_path TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
);

CREATE TABLE IF NOT EXISTS checkpoint_blobs (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    channel TEXT NOT NULL,
    version TEXT NOT NULL,
    type TEXT NOT NULL,
    blob BYTEA,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
);

-- 4a. LangGraph checkpointer thread_id indexes.
-- `AsyncPostgresSaver.setup()` would create these with CREATE INDEX CONCURRENTLY
-- (a DDL that cannot run inside a transaction block — see agents/langgraph/checkpointer.py).
-- Pre-creating them here is idempotent (IF NOT EXISTS) and lets setup() skip
-- the CONCURRENTLY branch on first run.
CREATE INDEX IF NOT EXISTS checkpoint_blobs_thread_id_idx
    ON checkpoint_blobs (thread_id);
CREATE INDEX IF NOT EXISTS checkpoint_writes_thread_id_idx
    ON checkpoint_writes (thread_id);
-- `idx_checkpoints_thread_id` (with the project's prefix) is also created above
-- alongside the `checkpoints` table. setup()'s `IF NOT EXISTS` migration
-- sees the index already exists and skips.

-- 4b. Pre-seed `checkpoint_migrations` to mark the CONCURRENTLY migrations done.
-- `AsyncPostgresSaver.setup()` reads `MAX(v)` from this table to decide which
-- migrations to run. With v=8 (latest CONCURRENTLY migration) already recorded,
-- setup() will only run the v=9 `ADD COLUMN task_path` migration, which is
-- idempotent (`IF NOT EXISTS`) and safe inside a transaction.
--
-- This avoids the deployment-time race where setup() crashes with
-- "CREATE INDEX CONCURRENTLY cannot run inside a transaction block".
CREATE TABLE IF NOT EXISTS checkpoint_migrations (
    v INTEGER PRIMARY KEY
);
INSERT INTO checkpoint_migrations (v) VALUES (0),(1),(2),(3),(4),(5),(6),(7),(8)
ON CONFLICT (v) DO NOTHING;

-- 5. agent_readonly role (run as superuser; see db/migrations/005_role.sql for password).
-- We only document the GRANTs here; the role itself must be created by a DBA
-- with `CREATE ROLE agent_readonly LOGIN PASSWORD '...'`.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agent_readonly') THEN
        GRANT CONNECT ON DATABASE anphat_commerce TO agent_readonly;
        GRANT USAGE ON SCHEMA public TO agent_readonly;
        GRANT SELECT ON products,
                            product_specs,
                            product_chunks,
                            product_prices,
                            product_spec_values,
                            product_current_prices,
                            graph_nodes,
                            graph_edges
            TO agent_readonly;
    END IF;
END
$$;
