-- M5 role bootstrap: create the agent_readonly role and grant minimal permissions.
-- Run as a Postgres superuser once before starting the agent.
-- Password must come from the secret manager; do NOT commit plaintext.

-- CREATE ROLE agent_readonly LOGIN PASSWORD :'agent_ro_password';
-- GRANT CONNECT ON DATABASE anphat_commerce TO agent_readonly;
-- GRANT USAGE ON SCHEMA public TO agent_readonly;
-- GRANT SELECT ON products,
--                     product_specs,
--                     product_chunks,
--                     product_prices,
--                     product_spec_values,
--                     product_current_prices,
--                     graph_nodes,
--                     graph_edges
--     TO agent_readonly;

-- After running 005_m5_agent_infra.sql, this file is a no-op for environments
-- where the role already exists. The role's password is sourced from AWS Secrets
-- Manager (or .env in dev) and never stored in this repository.
