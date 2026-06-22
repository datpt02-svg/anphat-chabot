-- M2 migration 003: add spec_index for list values, update unique constraint
-- M1: product_spec_values has UNIQUE (product_id, spec_key, normalized_key) NULLS NOT DISTINCT
-- M2: list values need spec_index to disambiguate multi-row keys (e.g. ports[0], ports[1])

ALTER TABLE product_spec_values
  ADD COLUMN IF NOT EXISTS spec_index integer NOT NULL DEFAULT 0;

ALTER TABLE product_spec_values
  DROP CONSTRAINT IF EXISTS uq_spec_values_product_key_norm;

ALTER TABLE product_spec_values
  ADD CONSTRAINT uq_spec_values_product_key_norm_idx
  UNIQUE NULLS NOT DISTINCT (product_id, spec_key, normalized_key, spec_index);
