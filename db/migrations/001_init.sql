-- M1 init migration: An Phat commerce PostgreSQL schema
-- Sections: extensions -> utility trigger -> tables -> view -> indexes -> triggers

-- 1. Extensions
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS unaccent;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_search;

-- 2. Utility trigger
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 3. Tables in dependency order

-- 3.1 crawl_runs
CREATE TABLE crawl_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source text NOT NULL DEFAULT 'anphatpc',
    status text NOT NULL DEFAULT 'running',
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    total_discovered integer DEFAULT 0,
    total_crawled integer DEFAULT 0,
    total_failed integer DEFAULT 0,
    total_normalized integer DEFAULT 0,
    input_paths jsonb NOT NULL DEFAULT '{}'::jsonb,
    config jsonb NOT NULL DEFAULT '{}'::jsonb,
    counts jsonb NOT NULL DEFAULT '{}'::jsonb,
    error_message text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT crawl_runs_status_check
        CHECK (status IN ('running', 'done', 'failed', 'partial'))
);

CREATE INDEX idx_crawl_runs_source ON crawl_runs (source);
CREATE INDEX idx_crawl_runs_status ON crawl_runs (status);
CREATE INDEX idx_crawl_runs_started_at ON crawl_runs (started_at DESC);

-- 3.2 raw_data
CREATE TABLE raw_data (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id uuid REFERENCES crawl_runs(id) ON DELETE SET NULL,
    source text NOT NULL DEFAULT 'anphatpc',
    source_url text,
    source_file text,
    line_number integer,
    payload jsonb NOT NULL,
    payload_hash text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_raw_data_run_id ON raw_data (run_id);
CREATE INDEX idx_raw_data_source_url ON raw_data (source_url);
CREATE INDEX idx_raw_data_payload_hash ON raw_data (payload_hash);
CREATE INDEX idx_raw_data_payload ON raw_data USING GIN (payload);

-- 3.3 products
CREATE TABLE products (
    id text PRIMARY KEY,
    source text NOT NULL DEFAULT 'anphatpc',
    source_url text NOT NULL UNIQUE,
    source_product_id text,
    sku text,
    slug text UNIQUE,
    name text NOT NULL,
    brand text,
    category text NOT NULL,
    subcategory text,
    thumbnail_url text,
    images jsonb NOT NULL DEFAULT '[]'::jsonb,
    price_vnd bigint,
    list_price_vnd bigint,
    sale_price_vnd bigint,
    build_pc_price_vnd bigint,
    regional_price_vnd bigint,
    stock_status text,
    stock_quantity integer,
    warranty_text text,
    warranty_months integer,
    description text,
    breadcrumbs jsonb NOT NULL DEFAULT '[]'::jsonb,
    raw_specs jsonb NOT NULL DEFAULT '{}'::jsonb,
    validation_warnings jsonb NOT NULL DEFAULT '[]'::jsonb,
    llm_warnings jsonb NOT NULL DEFAULT '[]'::jsonb,
    raw_html_path text,
    status text NOT NULL DEFAULT 'active',
    canonical_hash text,
    crawled_at timestamptz,
    normalized_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    deleted_at timestamptz,
    CONSTRAINT products_status_check
        CHECK (status IN ('active', 'hidden', 'discontinued', 'deleted')),
    CONSTRAINT products_price_vnd_nonneg
        CHECK (price_vnd IS NULL OR price_vnd >= 0),
    CONSTRAINT products_list_price_vnd_nonneg
        CHECK (list_price_vnd IS NULL OR list_price_vnd >= 0),
    CONSTRAINT products_sale_price_vnd_nonneg
        CHECK (sale_price_vnd IS NULL OR sale_price_vnd >= 0),
    CONSTRAINT products_build_pc_price_vnd_nonneg
        CHECK (build_pc_price_vnd IS NULL OR build_pc_price_vnd >= 0),
    CONSTRAINT products_regional_price_vnd_nonneg
        CHECK (regional_price_vnd IS NULL OR regional_price_vnd >= 0),
    CONSTRAINT products_warranty_months_nonneg
        CHECK (warranty_months IS NULL OR warranty_months >= 0),
    CONSTRAINT products_soft_delete_consistency
        CHECK (
            (status = 'deleted' AND deleted_at IS NOT NULL)
            OR (status <> 'deleted' AND deleted_at IS NULL)
        )
);

CREATE INDEX idx_products_category ON products (category);
CREATE INDEX idx_products_brand ON products (brand);
CREATE INDEX idx_products_price ON products (price_vnd);
CREATE INDEX idx_products_stock ON products (stock_status);
CREATE INDEX idx_products_sku ON products (sku);
CREATE INDEX idx_products_status ON products (status);
CREATE INDEX idx_products_canonical_hash ON products (canonical_hash);
CREATE INDEX idx_products_raw_specs ON products USING GIN (raw_specs);
CREATE INDEX idx_products_validation_warnings ON products USING GIN (validation_warnings);
CREATE INDEX idx_products_llm_warnings ON products USING GIN (llm_warnings);

CREATE TRIGGER trg_products_updated_at
    BEFORE UPDATE ON products
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- 3.4 product_specs
CREATE TABLE product_specs (
    product_id text PRIMARY KEY REFERENCES products(id) ON DELETE CASCADE,
    product_type text,
    model text,
    cpu_model text,
    cpu_cores integer,
    cpu_threads integer,
    cpu_base_clock_ghz numeric,
    cpu_boost_clock_ghz numeric,
    socket text,
    ram_gb integer,
    ram_type text,
    ram_speed_mhz integer,
    max_ram_gb integer,
    ram_slots integer,
    ram_standard text,
    storage_gb integer,
    storage_type text,
    storage_detail text,
    upgrade_storage_options jsonb NOT NULL DEFAULT '[]'::jsonb,
    gpu_model text,
    gpu_vram_gb integer,
    gpu_vram_type text,
    chipset text,
    form_factor text,
    psu_wattage_w integer,
    recommended_psu_w integer,
    supported_mainboard_form_factors jsonb NOT NULL DEFAULT '[]'::jsonb,
    max_gpu_length_mm integer,
    max_cpu_cooler_height_mm integer,
    screen_inches numeric,
    resolution_label text,
    resolution_width integer,
    resolution_height integer,
    refresh_rate_hz integer,
    panel_type text,
    os text,
    ports jsonb NOT NULL DEFAULT '[]'::jsonb,
    connectivity jsonb NOT NULL DEFAULT '[]'::jsonb,
    switch_type text,
    layout text,
    mouse_dpi integer,
    weight_kg numeric,
    confidence numeric,
    warnings jsonb NOT NULL DEFAULT '[]'::jsonb,
    specs jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT product_specs_confidence_range
        CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    CONSTRAINT product_specs_cpu_cores_nonneg
        CHECK (cpu_cores IS NULL OR cpu_cores >= 0),
    CONSTRAINT product_specs_cpu_threads_nonneg
        CHECK (cpu_threads IS NULL OR cpu_threads >= 0),
    CONSTRAINT product_specs_ram_gb_nonneg
        CHECK (ram_gb IS NULL OR ram_gb >= 0),
    CONSTRAINT product_specs_storage_gb_nonneg
        CHECK (storage_gb IS NULL OR storage_gb >= 0),
    CONSTRAINT product_specs_psu_wattage_nonneg
        CHECK (psu_wattage_w IS NULL OR psu_wattage_w >= 0),
    CONSTRAINT product_specs_recommended_psu_nonneg
        CHECK (recommended_psu_w IS NULL OR recommended_psu_w >= 0),
    CONSTRAINT product_specs_max_ram_gb_nonneg
        CHECK (max_ram_gb IS NULL OR max_ram_gb >= 0),
    CONSTRAINT product_specs_max_gpu_length_nonneg
        CHECK (max_gpu_length_mm IS NULL OR max_gpu_length_mm >= 0),
    CONSTRAINT product_specs_max_cpu_cooler_height_nonneg
        CHECK (max_cpu_cooler_height_mm IS NULL OR max_cpu_cooler_height_mm >= 0),
    CONSTRAINT product_specs_refresh_rate_nonneg
        CHECK (refresh_rate_hz IS NULL OR refresh_rate_hz >= 0),
    CONSTRAINT product_specs_mouse_dpi_nonneg
        CHECK (mouse_dpi IS NULL OR mouse_dpi >= 0)
);

CREATE INDEX idx_specs_model ON product_specs (model);
CREATE INDEX idx_specs_cpu_model ON product_specs (cpu_model);
CREATE INDEX idx_specs_socket ON product_specs (socket);
CREATE INDEX idx_specs_ram_gb ON product_specs (ram_gb);
CREATE INDEX idx_specs_ram_type ON product_specs (ram_type);
CREATE INDEX idx_specs_storage_gb ON product_specs (storage_gb);
CREATE INDEX idx_specs_storage_type ON product_specs (storage_type);
CREATE INDEX idx_specs_gpu_model ON product_specs (gpu_model);
CREATE INDEX idx_specs_form_factor ON product_specs (form_factor);
CREATE INDEX idx_specs_psu_wattage ON product_specs (psu_wattage_w);
CREATE INDEX idx_specs_recommended_psu ON product_specs (recommended_psu_w);
CREATE INDEX idx_specs_screen_inches ON product_specs (screen_inches);
CREATE INDEX idx_specs_refresh_rate ON product_specs (refresh_rate_hz);
CREATE INDEX idx_specs_chipset ON product_specs (chipset);
CREATE INDEX idx_specs_jsonb ON product_specs USING GIN (specs);
CREATE INDEX idx_specs_warnings ON product_specs USING GIN (warnings);

CREATE TRIGGER trg_product_specs_updated_at
    BEFORE UPDATE ON product_specs
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- 3.5 product_spec_values
CREATE TABLE product_spec_values (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id text NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    group_name text,
    spec_key text NOT NULL,
    normalized_key text,
    spec_value text,
    value_num numeric,
    unit text,
    confidence numeric,
    raw jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX uq_spec_values_product_key_norm
    ON product_spec_values (product_id, spec_key, normalized_key) NULLS NOT DISTINCT;

CREATE INDEX idx_spec_values_product_id ON product_spec_values (product_id);
CREATE INDEX idx_spec_values_key ON product_spec_values (spec_key);
CREATE INDEX idx_spec_values_normalized_key ON product_spec_values (normalized_key);
CREATE INDEX idx_spec_values_key_num ON product_spec_values (spec_key, value_num);
CREATE INDEX idx_spec_values_raw ON product_spec_values USING GIN (raw);

-- 3.6 product_prices
CREATE TABLE product_prices (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id text NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    crawl_run_id uuid REFERENCES crawl_runs(id) ON DELETE SET NULL,
    price_vnd bigint,
    list_price_vnd bigint,
    sale_price_vnd bigint,
    build_pc_price_vnd bigint,
    regional_price_vnd bigint,
    stock_status text,
    stock_quantity integer,
    price_hash text,
    captured_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT product_prices_price_vnd_nonneg
        CHECK (price_vnd IS NULL OR price_vnd >= 0),
    CONSTRAINT product_prices_list_price_vnd_nonneg
        CHECK (list_price_vnd IS NULL OR list_price_vnd >= 0),
    CONSTRAINT product_prices_sale_price_vnd_nonneg
        CHECK (sale_price_vnd IS NULL OR sale_price_vnd >= 0),
    CONSTRAINT product_prices_build_pc_price_vnd_nonneg
        CHECK (build_pc_price_vnd IS NULL OR build_pc_price_vnd >= 0),
    CONSTRAINT product_prices_regional_price_vnd_nonneg
        CHECK (regional_price_vnd IS NULL OR regional_price_vnd >= 0)
);

CREATE INDEX idx_product_prices_product_id ON product_prices (product_id);
CREATE INDEX idx_product_prices_captured_at ON product_prices (captured_at DESC);
CREATE INDEX idx_product_prices_product_captured
    ON product_prices (product_id, captured_at DESC, created_at DESC);
CREATE INDEX idx_product_prices_hash ON product_prices (price_hash);

-- 3.7 product_chunks
CREATE TABLE product_chunks (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id text NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    chunk_type text NOT NULL,
    chunk_index integer NOT NULL DEFAULT 0,
    content text NOT NULL,
    content_hash text,
    token_count integer,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    search_vector tsvector GENERATED ALWAYS AS
        (to_tsvector('simple', coalesce(content, ''))) STORED,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT product_chunks_chunk_type_check
        CHECK (chunk_type IN ('title', 'specs', 'description', 'selling_points',
                              'raw_specs', 'compatibility', 'warranty', 'debug')),
    CONSTRAINT product_chunks_token_count_nonneg
        CHECK (token_count IS NULL OR token_count >= 0)
);

CREATE UNIQUE INDEX uq_product_chunks_product_type_idx_hash
    ON product_chunks (product_id, chunk_type, chunk_index, content_hash);

CREATE INDEX idx_product_chunks_product_id ON product_chunks (product_id);
CREATE INDEX idx_product_chunks_type ON product_chunks (chunk_type);
CREATE INDEX idx_product_chunks_hash ON product_chunks (content_hash);
CREATE INDEX idx_product_chunks_search ON product_chunks USING GIN (search_vector);

-- ParadeDB BM25 index: chatbot keyword retrieval over product_chunks
-- ParadeDB 0.15.x syntax: tokenizer configured via text_fields JSON option
CREATE INDEX idx_product_chunks_bm25
    ON product_chunks
    USING bm25 (id, content)
    WITH (key_field='id', text_fields='{"content": {"tokenizer": {"type": "whitespace"}}}');

-- 3.8 product_embeddings
CREATE TABLE product_embeddings (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    chunk_id uuid NOT NULL REFERENCES product_chunks(id) ON DELETE CASCADE,
    product_id text NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    embedding vector(1024) NOT NULL,
    model_name text NOT NULL,
    dimension integer NOT NULL DEFAULT 1024,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT product_embeddings_dimension_check CHECK (dimension = 1024)
);

CREATE UNIQUE INDEX uq_product_embeddings_chunk_model
    ON product_embeddings (chunk_id, model_name);

CREATE INDEX idx_product_embeddings_product_id ON product_embeddings (product_id);
CREATE INDEX idx_product_embeddings_chunk_id ON product_embeddings (chunk_id);
CREATE INDEX idx_product_embeddings_model_name ON product_embeddings (model_name);
CREATE INDEX idx_product_embeddings_vector
    ON product_embeddings USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

CREATE TRIGGER trg_product_embeddings_updated_at
    BEFORE UPDATE ON product_embeddings
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- 3.9 graph_nodes
CREATE TABLE graph_nodes (
    id text PRIMARY KEY,
    type text NOT NULL,
    label text NOT NULL,
    properties jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_graph_nodes_type ON graph_nodes (type);
CREATE INDEX idx_graph_nodes_label ON graph_nodes (label);
CREATE INDEX idx_graph_nodes_properties ON graph_nodes USING GIN (properties);

-- 3.10 graph_edges
CREATE TABLE graph_edges (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    src text NOT NULL REFERENCES graph_nodes(id) ON DELETE CASCADE,
    relation text NOT NULL,
    dst text NOT NULL REFERENCES graph_nodes(id) ON DELETE CASCADE,
    properties jsonb NOT NULL DEFAULT '{}'::jsonb,
    properties_hash text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX uq_graph_edges_src_relation_dst_hash
    ON graph_edges (src, relation, dst, properties_hash);

CREATE INDEX idx_graph_edges_src ON graph_edges (src);
CREATE INDEX idx_graph_edges_dst ON graph_edges (dst);
CREATE INDEX idx_graph_edges_relation ON graph_edges (relation);
CREATE INDEX idx_graph_edges_src_relation ON graph_edges (src, relation);
CREATE INDEX idx_graph_edges_dst_relation ON graph_edges (dst, relation);
CREATE INDEX idx_graph_edges_properties_hash ON graph_edges (properties_hash);
CREATE INDEX idx_graph_edges_properties ON graph_edges USING GIN (properties);

-- 3.11 crawl_errors
CREATE TABLE crawl_errors (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id uuid REFERENCES crawl_runs(id) ON DELETE SET NULL,
    source_url text NOT NULL,
    stage text NOT NULL,
    error_type text,
    error_message text,
    raw jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_crawl_errors_run_id ON crawl_errors (run_id);
CREATE INDEX idx_crawl_errors_source_url ON crawl_errors (source_url);
CREATE INDEX idx_crawl_errors_stage ON crawl_errors (stage);
CREATE INDEX idx_crawl_errors_type ON crawl_errors (error_type);

-- 3.12 search_outbox
CREATE TABLE search_outbox (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type text NOT NULL,
    product_id text REFERENCES products(id) ON DELETE CASCADE,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'pending',
    attempts integer NOT NULL DEFAULT 0,
    error_message text,
    created_at timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz,
    CONSTRAINT search_outbox_event_type_check
        CHECK (event_type IN ('product_search_upsert', 'product_search_delete', 'index_rebuild_requested')),
    CONSTRAINT search_outbox_status_check
        CHECK (status IN ('pending', 'processing', 'done', 'failed'))
);

CREATE INDEX idx_search_outbox_status ON search_outbox (status);
CREATE INDEX idx_search_outbox_product_id ON search_outbox (product_id);
CREATE INDEX idx_search_outbox_created_at ON search_outbox (created_at);

-- 4. View: product_current_prices
CREATE VIEW product_current_prices AS
SELECT DISTINCT ON (product_id)
    product_id,
    crawl_run_id,
    price_vnd,
    list_price_vnd,
    sale_price_vnd,
    build_pc_price_vnd,
    regional_price_vnd,
    stock_status,
    stock_quantity,
    price_hash,
    captured_at,
    created_at
FROM product_prices
ORDER BY product_id, captured_at DESC, created_at DESC;
