BEGIN;

-- Idempotência operacional para jobs de indexação.
ALTER TABLE jobs
  ADD COLUMN IF NOT EXISTS idempotency_key TEXT NULL;

-- Antes de criar a restrição, falha de forma explícita caso já existam
-- duplicatas ativas do mesmo version_id + pipeline_version.
DO $$
DECLARE
  duplicate_groups bigint;
BEGIN
  SELECT COUNT(*)
  INTO duplicate_groups
  FROM (
    SELECT
      payload->>'version_id' AS version_id,
      payload->>'pipeline_version' AS pipeline_version
    FROM jobs
    WHERE type = 'IndexVersionJob'
      AND status IN ('queued', 'running', 'failed')
      AND NULLIF(payload->>'version_id', '') IS NOT NULL
      AND NULLIF(payload->>'pipeline_version', '') IS NOT NULL
    GROUP BY payload->>'version_id', payload->>'pipeline_version'
    HAVING COUNT(*) > 1
  ) d;

  IF duplicate_groups > 0 THEN
    RAISE EXCEPTION
      'Existem % grupos duplicados de IndexVersionJob ativos; contenha/cancele os jobs redundantes antes de aplicar 0004_index_job_idempotency.sql.',
      duplicate_groups
    USING HINT =
      'Consulte jobs type=IndexVersionJob com status queued/running/failed agrupados por payload.version_id + payload.pipeline_version.';
  END IF;
END $$;

-- Backfill auditável para jobs antigos que já carregam os dois componentes.
UPDATE jobs
SET idempotency_key =
    'IndexVersionJob:' ||
    (payload->>'pipeline_version') ||
    ':' ||
    (payload->>'version_id')
WHERE type = 'IndexVersionJob'
  AND idempotency_key IS NULL
  AND NULLIF(payload->>'version_id', '') IS NOT NULL
  AND NULLIF(payload->>'pipeline_version', '') IS NOT NULL;

-- Só existe uma execução ATIVA por versão + pipeline.
-- Jobs done/dead liberam a chave para uma nova ação operacional explícita.
CREATE UNIQUE INDEX IF NOT EXISTS uq_jobs_active_idempotency
  ON jobs(type, idempotency_key)
  WHERE idempotency_key IS NOT NULL
    AND status IN ('queued', 'running', 'failed');

CREATE INDEX IF NOT EXISTS ix_jobs_type_idempotency
  ON jobs(type, idempotency_key)
  WHERE idempotency_key IS NOT NULL;

COMMIT;
