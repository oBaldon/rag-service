BEGIN;

-- Extensões necessárias
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS unaccent;

-- =========================
-- Conteúdo e versionamento
-- =========================

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

  -- origem/auditoria (raw = URL)
  source_url TEXT NOT NULL,
  final_url TEXT NULL,
  http_status INT NULL,
  captured_at TIMESTAMPTZ NULL,

  -- deduplicação (apenas content_hash)
  content_hash TEXT NOT NULL,

  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Deduplicação por conteúdo extraído/normalizado
CREATE UNIQUE INDEX IF NOT EXISTS uq_document_versions_content_hash
  ON document_versions(content_hash);

CREATE INDEX IF NOT EXISTS ix_document_versions_document_id
  ON document_versions(document_id);

CREATE INDEX IF NOT EXISTS ix_document_versions_status
  ON document_versions(status);

-- =========================
-- Snapshot derivado: nodes
-- =========================

CREATE TABLE IF NOT EXISTS nodes (
  node_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  version_id UUID NOT NULL REFERENCES document_versions(version_id) ON DELETE CASCADE,

  kind TEXT NOT NULL DEFAULT 'heading_section',
  path TEXT NOT NULL,

  parent_id UUID NULL REFERENCES nodes(node_id) ON DELETE SET NULL,

  heading_text TEXT NOT NULL,
  heading_level INT NOT NULL CHECK (heading_level >= 1 AND heading_level <= 6),

  text_normalized TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_nodes_version_id ON nodes(version_id);
CREATE INDEX IF NOT EXISTS ix_nodes_parent_id ON nodes(parent_id);
CREATE INDEX IF NOT EXISTS ix_nodes_path ON nodes(path);

-- =========================
-- Chunks + FTS
-- =========================

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

  tsv TSVECTOR GENERATED ALWAYS AS (to_tsvector('portuguese', coalesce(text,''))) STORED
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_embedding_chunks_version_pipeline_hash
  ON embedding_chunks(version_id, pipeline_version, chunk_hash);

CREATE INDEX IF NOT EXISTS ix_embedding_chunks_version_id
  ON embedding_chunks(version_id);

CREATE INDEX IF NOT EXISTS ix_embedding_chunks_tsv_gin
  ON embedding_chunks USING GIN (tsv);

-- =========================
-- Embeddings (pgvector)
-- =========================

CREATE TABLE IF NOT EXISTS chunk_embeddings (
  chunk_id UUID NOT NULL REFERENCES embedding_chunks(chunk_id) ON DELETE CASCADE,
  embedding_model_id TEXT NOT NULL,
  pipeline_version TEXT NOT NULL,
  embedding VECTOR(1536) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (chunk_id, embedding_model_id, pipeline_version)
);

-- Index auxiliar para filtrar por modelo/pipeline
CREATE INDEX IF NOT EXISTS ix_chunk_embeddings_model_pipeline
  ON chunk_embeddings(embedding_model_id, pipeline_version);

-- Índice vetorial HNSW (cosine) - único para o MVP
CREATE INDEX IF NOT EXISTS ix_chunk_embeddings_hnsw
  ON chunk_embeddings USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);

-- =========================
-- Fila no Postgres: jobs
-- =========================

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

-- =========================
-- Auditoria: rag_runs
-- =========================

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
