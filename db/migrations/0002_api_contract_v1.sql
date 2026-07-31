BEGIN;

ALTER TABLE rag_runs
  ADD COLUMN IF NOT EXISTS request_id TEXT;

UPDATE rag_runs
SET request_id = run_id::text
WHERE request_id IS NULL OR btrim(request_id) = '';

ALTER TABLE rag_runs
  ALTER COLUMN request_id SET NOT NULL;

CREATE INDEX IF NOT EXISTS ix_rag_runs_request_id
  ON rag_runs(request_id);

COMMIT;
