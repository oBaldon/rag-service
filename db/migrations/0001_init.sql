BEGIN;

-- =========================================================
-- Extensões
-- =========================================================
DO $$
DECLARE
  ext text;
BEGIN
  FOREACH ext IN ARRAY ARRAY['pgcrypto','unaccent','vector'] LOOP
    BEGIN
      EXECUTE format('CREATE EXTENSION IF NOT EXISTS %I', ext);
    EXCEPTION
      WHEN insufficient_privilege THEN
        RAISE EXCEPTION
          'Sem permissão para CREATE EXTENSION % (db=%, role=%).',
          ext, current_database(), current_user
        USING HINT =
          'Rode uma vez como superuser: psql -U postgres -p 5555 -d intelireg -c "CREATE EXTENSION IF NOT EXISTS '
          || ext ||
          ';"  (e garanta que o pacote/extensão está instalado no servidor).';
      WHEN undefined_file THEN
        RAISE EXCEPTION
          'Extensão % não está instalada no servidor (db=%).',
          ext, current_database()
        USING HINT =
          'Instale a extensão no sistema (ex: pacote pgvector do seu Postgres) e depois execute CREATE EXTENSION como superuser.';
    END;
  END LOOP;
END $$;

-- =========================================================
-- Conteúdo e versionamento
-- =========================================================

CREATE TABLE IF NOT EXISTS documents (
  document_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  title TEXT NOT NULL,
  source_org TEXT NOT NULL,
  doc_type TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS document_versions (
  version_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id UUID NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,

  status TEXT NOT NULL CHECK (status IN ('READY_FOR_INDEX','INDEXED')),

  source_url TEXT NOT NULL,
  final_url TEXT NULL,
  http_status INT NULL,
  captured_at TIMESTAMPTZ NULL,

  content_hash TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Se já existia um índice não-unique com esse nome em algum DB antigo, garante upgrade:
DROP INDEX IF EXISTS uq_document_versions_content_hash;
CREATE UNIQUE INDEX IF NOT EXISTS uq_document_versions_content_hash
  ON document_versions(content_hash);

CREATE INDEX IF NOT EXISTS ix_document_versions_document_id
  ON document_versions(document_id);

CREATE INDEX IF NOT EXISTS ix_document_versions_status
  ON document_versions(status);

-- =========================================================
-- Snapshot derivado: nodes (node_index desde o início)
-- =========================================================

CREATE TABLE IF NOT EXISTS nodes (
  node_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  version_id UUID NOT NULL REFERENCES document_versions(version_id) ON DELETE CASCADE,

  node_index INT NOT NULL,

  kind TEXT NOT NULL DEFAULT 'heading_section',
  path TEXT NOT NULL,

  parent_id UUID NULL REFERENCES nodes(node_id) ON DELETE SET NULL,

  heading_text TEXT NOT NULL,
  heading_level INT NOT NULL CHECK (heading_level >= 1 AND heading_level <= 6),

  text_normalized TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_nodes_version_id ON nodes(version_id);
CREATE INDEX IF NOT EXISTS ix_nodes_parent_id ON nodes(parent_id);

-- (se alguém tentar inserir nodes repetidos na mesma version, o banco bloqueia)
DROP INDEX IF EXISTS ix_nodes_version_node_index;
CREATE UNIQUE INDEX IF NOT EXISTS uq_nodes_version_node_index
  ON nodes(version_id, node_index);

DROP INDEX IF EXISTS ix_nodes_path;
CREATE UNIQUE INDEX IF NOT EXISTS uq_nodes_version_path
  ON nodes(version_id, path);

-- (opcional) se você ainda quiser busca por path sem filtrar por version:
-- CREATE INDEX IF NOT EXISTS ix_nodes_path_only ON nodes(path);

-- =========================================================
-- Chunks + FTS
-- =========================================================

CREATE TABLE IF NOT EXISTS embedding_chunks (
  chunk_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  version_id UUID NOT NULL REFERENCES document_versions(version_id) ON DELETE CASCADE,

  pipeline_version TEXT NOT NULL,
  chunk_index INT NOT NULL,
  chunk_hash TEXT NOT NULL,

  text TEXT NOT NULL,
  node_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
  tokens_count INT NOT NULL,

  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

  tsv TSVECTOR NOT NULL DEFAULT ''::tsvector,

  CONSTRAINT uq_embedding_chunks_version_pipeline_hash
    UNIQUE (version_id, pipeline_version, chunk_hash)
);

CREATE INDEX IF NOT EXISTS ix_embedding_chunks_version_id
  ON embedding_chunks(version_id);

CREATE INDEX IF NOT EXISTS ix_embedding_chunks_tsv_gin
  ON embedding_chunks USING GIN (tsv);

CREATE OR REPLACE FUNCTION embedding_chunks_set_tsv()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  NEW.tsv := to_tsvector('portuguese', unaccent(coalesce(NEW.text,'')));
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_embedding_chunks_set_tsv ON embedding_chunks;

CREATE TRIGGER trg_embedding_chunks_set_tsv
BEFORE INSERT OR UPDATE OF text
ON embedding_chunks
FOR EACH ROW
EXECUTE FUNCTION embedding_chunks_set_tsv();

-- =========================================================
-- Embeddings (pgvector)
-- =========================================================

CREATE TABLE IF NOT EXISTS chunk_embeddings (
  chunk_id UUID NOT NULL REFERENCES embedding_chunks(chunk_id) ON DELETE CASCADE,
  embedding_model_id TEXT NOT NULL,
  pipeline_version TEXT NOT NULL,
  embedding VECTOR(1536) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (chunk_id, embedding_model_id, pipeline_version)
);

CREATE INDEX IF NOT EXISTS ix_chunk_embeddings_model_pipeline
  ON chunk_embeddings(embedding_model_id, pipeline_version);

DO $$
BEGIN
  EXECUTE '
    CREATE INDEX IF NOT EXISTS ix_chunk_embeddings_hnsw
      ON chunk_embeddings USING hnsw (embedding vector_cosine_ops)
      WITH (m = 16, ef_construction = 64)
  ';
EXCEPTION
  WHEN undefined_object OR invalid_parameter_value OR feature_not_supported THEN
    RAISE NOTICE 'HNSW não disponível no pgvector atual; seguindo sem índice vetorial HNSW.';
END $$;

-- =========================================================
-- Fila no Postgres: jobs
-- =========================================================

CREATE TABLE IF NOT EXISTS jobs (
  job_id BIGSERIAL PRIMARY KEY,
  type TEXT NOT NULL,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,

  status TEXT NOT NULL CHECK (status IN ('queued','running','done','failed','dead')) DEFAULT 'queued',
  attempts INT NOT NULL DEFAULT 0,
  run_after TIMESTAMPTZ NOT NULL DEFAULT now(),

  locked_at TIMESTAMPTZ NULL,
  locked_by TEXT NULL,

  last_error TEXT NULL,

  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_jobs_status_run_after
  ON jobs(status, run_after);

CREATE INDEX IF NOT EXISTS ix_jobs_locked_at
  ON jobs(locked_at);

CREATE OR REPLACE FUNCTION jobs_set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_jobs_set_updated_at ON jobs;

CREATE TRIGGER trg_jobs_set_updated_at
BEFORE UPDATE ON jobs
FOR EACH ROW
EXECUTE FUNCTION jobs_set_updated_at();

-- =========================================================
-- Auditoria: rag_runs
-- =========================================================

CREATE TABLE IF NOT EXISTS rag_runs (
  run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  asked_at TIMESTAMPTZ NOT NULL DEFAULT now(),

  question TEXT NOT NULL,
  filters JSONB NOT NULL DEFAULT '{}'::jsonb,
  retrieval_params JSONB NOT NULL DEFAULT '{}'::jsonb,

  embedding_model_id TEXT NOT NULL,
  llm_model_id TEXT NOT NULL,
  pipeline_version TEXT NOT NULL,

  selected JSONB NOT NULL DEFAULT '[]'::jsonb,
  answer_text TEXT NOT NULL,
  insufficient_evidence BOOLEAN NOT NULL DEFAULT false,

  result_json JSONB NOT NULL,
  result_hash TEXT NOT NULL,

  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_rag_runs_asked_at ON rag_runs(asked_at);

COMMIT;
