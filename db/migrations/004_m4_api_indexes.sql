-- M4 API indexes: optimize read paths for FastAPI catalog endpoints.
-- products(category, updated_at DESC): GET /api/products/{slug}/related
-- product_spec_values(product_id, group_name): GET /api/products/{slug} grouped specs

CREATE INDEX IF NOT EXISTS idx_products_category_updated_at
    ON products (category, updated_at DESC)
    WHERE status = 'active' AND deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_spec_values_product_group
    ON product_spec_values (product_id, group_name);
