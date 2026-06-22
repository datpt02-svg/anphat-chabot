-- M2 migration 002: partial index for active crawl_runs (resume debug)
CREATE INDEX IF NOT EXISTS idx_crawl_runs_active
  ON crawl_runs (started_at DESC)
  WHERE status IN ('running', 'partial');
