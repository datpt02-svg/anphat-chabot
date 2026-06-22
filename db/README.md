# Database (M1)

PostgreSQL source of truth cho dữ liệu An Phát. M1 chỉ khởi tạo schema + local DB; chưa import data, chưa có API/UI.

## Stack

- PostgreSQL 16 (image `paradedb/paradedb:0.15.26-pg16` — pinned tag, không dùng `latest`).
- Extensions: `pgcrypto`, `unaccent`, `vector`, `pg_search` (ParadeDB BM25).
- Default DB: `anphat_commerce`, user `anphat`, port `5432`.

## Cấu trúc

```
db/
├── README.md
└── migrations/
    └── 001_init.sql   -- schema M1: 12 bảng + 1 view
```

## 1. Khởi động Postgres

```powershell
docker compose up -d postgres
```

Container tên `anphat-postgres`. Đợi healthcheck `pg_isready` pass.

## 2. Áp migration

### Cách A: host có `psql`

```powershell
psql "postgresql://anphat:anphat_dev_password@localhost:5432/anphat_commerce" -f db/migrations/001_init.sql
```

### Cách B: Docker fallback (không có `psql` trên host)

```powershell
docker compose cp db/migrations/001_init.sql postgres:/tmp/001_init.sql
docker compose exec postgres psql -U anphat -d anphat_commerce -f /tmp/001_init.sql
```

## 3. Verify

```powershell
docker compose exec postgres psql -U anphat -d anphat_commerce -c "select extname from pg_extension where extname in ('pgcrypto','unaccent','vector','pg_search') order by extname;"
docker compose exec postgres psql -U anphat -d anphat_commerce -c "select count(*) as base_table_count from information_schema.tables where table_schema='public' and table_type='BASE TABLE' and table_name <> 'spatial_ref_sys';"
docker compose exec postgres psql -U anphat -d anphat_commerce -c "select table_name from information_schema.views where table_schema='public' and table_name='product_current_prices';"
docker compose exec postgres psql -U anphat -d anphat_commerce -c "select indexname from pg_indexes where tablename='product_chunks' and indexdef ilike '%USING bm25%';"
```

Expected:

- extensions: 4 rows
- base tables (excluding PostGIS `spatial_ref_sys`): 12
- view: `product_current_prices`
- BM25 index: `idx_product_chunks_bm25`

Note: ParadeDB image bundles PostGIS → `spatial_ref_sys` xuất hiện tự động. Đó là internal của PostGIS, không phải schema M1.

## 4. Schema purpose (12 tables + 1 view)

| Bảng | Mục đích |
|---|---|
| `crawl_runs` | Track từng lần import/crawl, status + counters |
| `raw_data` | Lưu raw row gốc từ crawler output (provenance/debug) |
| `products` | Product truth: mọi top-level field của `products_final.json` |
| `product_specs` | Wide columns + full `normalized_specs` JSONB |
| `product_spec_values` | Long-tail raw/normalized specs (key/value) |
| `product_prices` | Append-only history cho mọi loại giá + stock |
| `product_chunks` | Search/RAG chunks: `tsvector` (debug/fallback), BM25 (chatbot), pgvector (semantic) |
| `product_embeddings` | Multi-model embeddings (vector(1024)) |
| `graph_nodes` | Generic graph nodes |
| `graph_edges` | Generic graph edges (multi-evidence) |
| `crawl_errors` | Row-level import errors |
| `search_outbox` | Outbox cho M3 Meilisearch sync |
| `product_current_prices` (view) | Latest price/stock per product |

## 5. Quy tắc nghiệp vụ M2 phải tuân theo

- `products.sku` **không unique** (đã confirm duplicate trong M0). Unique key là `products.source_url`.
- `products.id` M2 phải derive deterministic: `source + ':' + sha256(source_url)[:16]`, ví dụ `anphatpc:abc123...`. KHÔNG dùng placeholder.
- `normalized_specs` lưu ở `product_specs.specs` (JSONB) + wide columns; KHÔNG đẩy vào `products`.
- Hai warning layer tách biệt:
  - `products.llm_warnings` (top-level crawler/final catalog warnings)
  - `product_specs.warnings` (`normalized_specs.warnings` về confidence/missing fields)
- `products.brand` là brand hiển thị/source-of-truth. LLM-cleaned brand chỉ nằm trong `product_specs.specs->brand`.
- `products.llm_warnings` ≠ `product_specs.warnings`. M2 phải ghi đúng layer.
- Hash algorithm: **sha256 hex lowercase**, computed ở app layer (M2).
  - `payload_hash` (raw_data)
  - `canonical_hash` (products)
  - `price_hash` (product_prices)
  - `content_hash` (product_chunks)
  - `properties_hash` (graph_edges)

## 6. PostgreSQL version

Yêu cầu PostgreSQL **15+** vì dùng `NULLS NOT DISTINCT` cho `product_spec_values` unique. Image `paradedb/paradedb:0.15.26-pg16` (pinned tag, không dùng `latest`) thỏa mãn.

## 6.1. Search role split

Mỗi search engine phụ trách một vai trò riêng:

| Engine | Vai trò | Nơi dùng |
|---|---|---|
| **Meilisearch** | Public search bar UX: facets, typo tolerance, instant product listing | Frontend search box (M3) |
| **ParadeDB BM25** (`pg_search`) | Chatbot keyword retrieval trên `product_chunks` (in-Postgres) | RAG/agent retrieval (M3+) |
| **pgvector** | Semantic retrieval trên `product_embeddings` | Similarity search, paraphrase queries |
| **tsvector** (PostgreSQL built-in) | Fallback/debug FTS, không dùng cho primary ranking | Debug, ad-hoc queries |

### BM25 query syntax (ParadeDB 0.15.x)

```sql
-- keyword match
SELECT id, paradedb.score(id) AS bm25_score
FROM product_chunks
WHERE product_chunks.content @@@ 'Test Product'
ORDER BY bm25_score DESC;
```

- Operator: `@@@` (BM25 match).
- Score function: `paradedb.score(id)` (KHÔNG dùng `pdb.score` — schema đó không tồn tại trong 0.15.x).
- Index: `idx_product_chunks_bm25` với tokenizer `whitespace` cấu hình qua `text_fields` JSON option.
- BM25 ranking chỉ dùng khi cùng `WHERE` đã filter/aggregate trước đó.

**Index DDL chính xác cho ParadeDB 0.15.26:**

```sql
CREATE INDEX idx_product_chunks_bm25
    ON product_chunks
    USING bm25 (id, content)
    WITH (key_field='id', text_fields='{"content": {"tokenizer": {"type": "whitespace"}}}');
```

> Plan gốc viết `(content::pdb.whitespace)` và `pdb.score(id)` — đó là syntax cũ của ParadeDB 0.10–0.13. Trong 0.15.x schema đổi sang `paradedb` và tokenizer config chuyển sang JSON `text_fields`. M1 đã verify cú pháp mới qua test trên image đã pin.

## 7. Embedding dimension

M1 dùng `vector(1024)`. `product_embeddings.dimension` có `CHECK (dimension = 1024)`. Nếu đổi model dim khác phải tạo migration mới điều chỉnh cả column `vector(N)` lẫn `CHECK` constraint.

### Embedding re-embed flow

- Cùng `(chunk_id, model_name)`: UPDATE row hiện có, refresh `updated_at`.
- Model khác cùng chunk: INSERT row mới cho chunk đó.
- Nội dung chunk đổi: INSERT chunk row mới (`content_hash` mới) → INSERT embedding row mới.

Vector search dùng cosine operator `<=>` của pgvector.

## 8. BM25 extension (ParadeDB `pg_search`)

Migration bật extension `pg_search` và tạo index `idx_product_chunks_bm25` trên `product_chunks(content)` với tokenizer `whitespace`. Mọi BM25 query trong chatbot phải dùng operator `@@@` và `paradedb.score(id)`. Tokenizer có thể nâng cấp (vd `ngram` cho tiếng Nhật, custom analyzer) qua migration sau, không tự ý đổi trong M2.

## 8. M2 idempotency strategy

- **`product_spec_values`**: delete tất cả rows của `product_id` rồi reinsert trong cùng transaction upsert product. Unique `(product_id, spec_key, normalized_key) NULLS NOT DISTINCT` chặn duplicate trong cùng product.
- **`product_prices`**: append history, KHÔNG unique `(product_id, price_hash)` (giá có thể quay lại giá trị cũ). M2 chỉ insert khi `price_hash` của latest row khác với hash mới.
- **`product_prices.captured_at`**: M2 phải dùng parsed `crawled_at` từ product nếu có, fallback `now()`.
- **Graph edges**: nếu `properties = '{}'`, M2 chỉ insert một edge per `(src, relation, dst)` vì `properties_hash` giống nhau.

## 9. Chunk types

`product_chunks.chunk_type` CHECK strict: `('title', 'specs', 'description', 'selling_points', 'raw_specs', 'compatibility', 'warranty', 'debug')`. Thêm chunk type mới phải qua migration.

## 10. Out of scope cho M1

- Không import data (`data/anphat/products_final.json` M2 sẽ xử lý).
- Không tạo graph data (graph derived từ specs ở M2).
- Không có Meilisearch, API, UI, agent.
- Chưa có Alembic; 1 SQL migration đủ cho M1.

## 11. Secrets

`.env` KHÔNG commit. Dùng `.env.example` làm mẫu.

## 12. M3 Meilisearch search layer

M3 thêm read-optimized search index `products` chạy trên Meilisearch v1.14 (Docker). Postgresql vẫn là source of truth; Meili chỉ chứa denormalized docs được build từ `products` + `product_specs` + `product_current_prices`.

### Verified versions (pinned)

- Docker image: `getmeili/meilisearch:v1.14` (pinned, không dùng `latest`).
- Python SDK: `meilisearch==0.34.1` (pinned trong `pyproject.toml`).

### Khởi động Meilisearch

```powershell
docker compose up -d postgres meilisearch
```

Container tên `anphat-meilisearch`. Healthcheck dùng `wget --spider http://localhost:7700/health` (Meili image không có `curl`). Master key mặc định `anphat_meili_dev_master_key` (override qua `MEILI_MASTER_KEY`).

### M3 CLI

```powershell
# 1. Connectivity check (DB + Meili + index tồn tại)
uv run python scripts/load_m3.py check

# 2. Tạo index + apply settings (idempotent — read-compares trước khi update)
uv run python scripts/load_m3.py setup-index

# 3. Rebuild toàn bộ index từ PostgreSQL (delete-by-source rồi add)
uv run python scripts/load_m3.py rebuild --source anphatpc

# 4. Search smoke
uv run python scripts/load_m3.py search --q "laptop i5 16gb" --source anphatpc --limit 10

# 5. Verify index khớp PostgreSQL
uv run python scripts/load_m3.py verify --source anphatpc

# 6. Backfill search_outbox cho incremental sync
uv run python scripts/load_m3.py enqueue-all --source anphatpc

# 7. Drain pending outbox events → Meili
uv run python scripts/load_m3.py sync --source anphatpc --limit 5000

# 8. Re-queue events kẹt ở status `processing` quá lâu
uv run python scripts/load_m3.py requeue-stale --older-than-minutes 15
```

### M3 module map

```
scripts/m3_search/
├── config.py        -- env, source resolution, constants
├── db.py            -- re-export M2 db helpers
├── meili.py         -- SDK wrapper (create_index, wait_for_task, delete by filter, etc.)
├── index_settings.py -- SEARCHABLE/FILTERABLE/SORTABLE/DISPLAYED + DESIRED_SETTINGS
├── documents.py     -- row → Meili doc, normalize_breadcrumbs, build_normalized_tokens, sanitize_id
├── sync.py          -- setup_index, enqueue_all, sync_pending, rebuild, requeue_stale
├── search.py        -- build_filter, build_sort, search_products
├── fallback.py      -- ParadeDB BM25 + tsvector fallback (distinct product dedup)
└── verify.py        -- connectivity, count match, smoke, facets, filters
```

### M3 invariants (locked trong M3)

- `products.id` format `source:hash` không phải Meili document ID hợp lệ (`:` không nằm trong alphabet `[a-zA-Z0-9_-]`). `build_document.sanitize_id()` thay `:` bằng `_`. Original id lưu ở field `product_id` để roundtrip.
- `id` được thêm vào `FILTERABLE_ATTRIBUTES` để cho phép `delete_documents_by_ids()` dùng filter `id IN [...]` (thay cho `delete_documents(ids=...)` đã deprecated trong SDK 0.34.1).
- Rebuild strategy: **delete-by-source** (`index.delete_documents(filter=...)`) → wait task → re-add in batches → wait all tasks → clear outbox. Downtime tạm thời cho source đó; chấp nhận cho M3 local.
- Verify dùng `index.search("", filter=f"source = '{source}'", limit=0).estimatedTotalHits` — KHÔNG dùng global stats khi index share nhiều source.
- `settings_match` so sánh `searchableAttributes` và `rankingRules` theo thứ tự (Meili dùng order cho priority), còn `filterableAttributes`/`sortableAttributes`/`displayedAttributes` so sánh theo set (Meili có thể reorder khi apply).
- `sync_pending` chỉ claim events cho 1 source qua `JOIN products p ON p.id = so.product_id WHERE p.source = %s`.
- `index_rebuild_requested` event type tồn tại trong schema nhưng `sync` mark failed với `error_message = 'index_rebuild_requested_out_of_scope'` (chưa có rebuild watcher trong M3).

### Tests

```powershell
# Unit (no DB, no Meili)
uv run pytest -q tests/test_m3_documents.py tests/test_m3_filters.py tests/test_m3_settings.py

# Integration (DB + Meili required)
uv run pytest -q tests/test_m3_sync.py tests/test_m3_search.py -m integration
```

`tests/conftest.py` tự động:
- mark `test_m3_*` với markers `integration` + `requires_meili`.
- set `M3_TESTING=1` + `M3_TEST_SOURCE=<uuid>` cho mỗi test.
- cleanup `search_outbox` + `products` + Meili docs theo source sau mỗi test.

### Troubleshooting

| Triệu chứng | Nguyên nhân | Fix |
|---|---|---|
| `MEILI_HOST not set` | Chưa copy `.env.example` → `.env` | `cp .env.example .env` |
| `rebuild count mismatch: db=8 meili=0` | Meili task chưa xong khi count check | Chạy lại `rebuild` (idempotent) |
| `Document identifier ... is invalid` | Test dùng id chứa `:` | Dùng `sanitize_id()` (đã auto trong `build_document`) |
| `meili cleanup failed ... MeilisearchApiError` | Task fail trong teardown | Xem log, kiểm tra master key |
| `pg_isready` fail | Postgres container chưa healthy | `docker compose up -d postgres` đợi healthcheck |
| `paradedb.score()` aggregate fail | `paradedb.score` là window function, không aggregate | Dùng `DISTINCT ON` form (đã là primary trong `fallback.py`) |
| Outbox `processing` kẹt sau crash | Worker chết giữa chừng | `requeue-stale --older-than-minutes 15` |
| Test fail `duplicate key value violates unique constraint "products_source_url_key"` | DB có data từ test trước chưa cleanup | Cleanup thủ công: `DELETE FROM products WHERE source LIKE 'anphatpc_test_%'` |
| `Healthcheck failing` cho meilisearch container | Image không có `curl` | Đã sửa: dùng `wget --spider` |

### M3 out of scope

- React UI / InstantSearch widgets
- FastAPI HTTP endpoint (chỉ CLI ở M3)
- Production zero-downtime alias swap
- Scheduled requeue-stale cron
- Vietnamese synonym dictionary beyond small `normalized_tokens`

