#!/usr/bin/env bash
set -euo pipefail

: "${DATABASE_URL:?DATABASE_URL não definido. Ex: postgresql://intelireg:intelireg@localhost:5555/intelireg}"

if [[ "${1:-}" != "--yes" ]]; then
  echo "Isso vai APAGAR os dados do MVP (TRUNCATE):"
  echo "  chunk_embeddings, embedding_chunks, nodes, document_versions, documents, jobs, rag_runs"
  echo
  read -r -p "Digite RESET para confirmar: " ans
  if [[ "$ans" != "RESET" ]]; then
    echo "Cancelado."
    exit 1
  fi
fi

psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -P pager=off <<'SQL'
BEGIN;
TRUNCATE TABLE
  chunk_embeddings,
  embedding_chunks,
  nodes,
  document_versions,
  documents,
  jobs,
  rag_runs
RESTART IDENTITY;
COMMIT;
SQL

echo "OK: banco resetado."
