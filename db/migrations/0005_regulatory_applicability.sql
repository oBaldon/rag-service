BEGIN;

-- =========================================================
-- RAG-QUALITY-03: aplicabilidade regulatória curada
-- =========================================================
-- Estes objetos armazenam FATOS CURADOS/REVISADOS sobre situação e relações
-- normativas. O retrieval/LLM não deve inferir vigência automaticamente a
-- partir destes campos quando não houver assertion aprovada.

CREATE TABLE IF NOT EXISTS regulatory_status_assertions (
  assertion_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  assertion_key TEXT NOT NULL UNIQUE,

  document_id UUID NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
  evidence_version_id UUID NULL REFERENCES document_versions(version_id) ON DELETE SET NULL,

  status TEXT NOT NULL CHECK (
    status IN (
      'vigente',
      'parcialmente_vigente',
      'revogada',
      'suspensa',
      'substituida',
      'sem_efeito'
    )
  ),

  effective_from DATE NULL,
  valid_to DATE NULL,

  review_status TEXT NOT NULL DEFAULT 'draft' CHECK (
    review_status IN ('draft', 'approved', 'rejected')
  ),

  source_url TEXT NULL,
  evidence_note TEXT NULL,

  asserted_by TEXT NOT NULL,
  reviewed_by TEXT NULL,
  reviewed_at TIMESTAMPTZ NULL,

  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT ck_regulatory_status_period
    CHECK (valid_to IS NULL OR effective_from IS NULL OR valid_to >= effective_from),

  CONSTRAINT ck_regulatory_status_approved_review
    CHECK (
      review_status <> 'approved'
      OR (
        NULLIF(btrim(reviewed_by), '') IS NOT NULL
        AND reviewed_at IS NOT NULL
        AND (
          NULLIF(btrim(source_url), '') IS NOT NULL
          OR evidence_version_id IS NOT NULL
          OR NULLIF(btrim(evidence_note), '') IS NOT NULL
        )
      )
    )
);

CREATE INDEX IF NOT EXISTS ix_regulatory_status_document
  ON regulatory_status_assertions(document_id, review_status);

-- Uma norma não deve possuir duas assertions "atuais" aprovadas concorrentes.
-- Histórico fechado (valid_to preenchido) continua permitido.
CREATE UNIQUE INDEX IF NOT EXISTS uq_regulatory_status_approved_open
  ON regulatory_status_assertions(document_id)
  WHERE review_status = 'approved'
    AND valid_to IS NULL;


CREATE TABLE IF NOT EXISTS regulatory_relations (
  relation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  relation_key TEXT NOT NULL UNIQUE,

  -- Direção canônica: source_document pratica a relação sobre target_document.
  -- Ex.: A --revoga--> B.
  source_document_id UUID NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
  target_document_id UUID NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
  evidence_version_id UUID NULL REFERENCES document_versions(version_id) ON DELETE SET NULL,

  relation_type TEXT NOT NULL CHECK (
    relation_type IN (
      'altera',
      'revoga',
      'revoga_parcialmente',
      'substitui',
      'regulamenta',
      'complementa',
      'prorroga',
      'corrige',
      'referencia'
    )
  ),

  effective_date DATE NULL,
  scope_note TEXT NULL,

  review_status TEXT NOT NULL DEFAULT 'draft' CHECK (
    review_status IN ('draft', 'approved', 'rejected')
  ),

  source_url TEXT NULL,
  evidence_note TEXT NULL,

  asserted_by TEXT NOT NULL,
  reviewed_by TEXT NULL,
  reviewed_at TIMESTAMPTZ NULL,

  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT ck_regulatory_relation_distinct_documents
    CHECK (source_document_id <> target_document_id),

  CONSTRAINT ck_regulatory_relation_approved_review
    CHECK (
      review_status <> 'approved'
      OR (
        NULLIF(btrim(reviewed_by), '') IS NOT NULL
        AND reviewed_at IS NOT NULL
        AND (
          NULLIF(btrim(source_url), '') IS NOT NULL
          OR evidence_version_id IS NOT NULL
          OR NULLIF(btrim(evidence_note), '') IS NOT NULL
        )
      )
    )
);

CREATE INDEX IF NOT EXISTS ix_regulatory_relations_source
  ON regulatory_relations(source_document_id, review_status, relation_type);

CREATE INDEX IF NOT EXISTS ix_regulatory_relations_target
  ON regulatory_relations(target_document_id, review_status, relation_type);

-- relation_key é a identidade de curadoria; esta restrição adicional impede
-- duas relações canônicas iguais com chaves diferentes.
CREATE UNIQUE INDEX IF NOT EXISTS uq_regulatory_relation_canonical
  ON regulatory_relations(source_document_id, target_document_id, relation_type);

COMMIT;
