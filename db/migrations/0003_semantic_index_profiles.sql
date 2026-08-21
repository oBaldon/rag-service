BEGIN;

CREATE TABLE IF NOT EXISTS index_profiles (
  pipeline_version TEXT NOT NULL,
  embedding_model_id TEXT NOT NULL,
  semantic_passage_enrichment BOOLEAN NOT NULL DEFAULT false,
  semantic_vocabulary_version TEXT NULL,
  semantic_embedding_profile_hash TEXT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

  PRIMARY KEY (pipeline_version, embedding_model_id)
);

COMMIT;
