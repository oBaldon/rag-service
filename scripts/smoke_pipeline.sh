#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'TXT'
Uso:
  scripts/smoke_pipeline.sh --url <URL> [--source-org ANVISA] [--doc-type site] [--reset]

Pré-requisitos:
  - estar na raiz do repo (onde existe src/)
  - DATABASE_URL exportado (ex.: postgresql://intelireg:intelireg@localhost:5555/intelireg)
  - venv ativada (opcional, mas recomendado)

Exemplos:
  export DATABASE_URL="postgresql://intelireg:intelireg@localhost:5555/intelireg"
  bash scripts/smoke_pipeline.sh --reset --url "https://www.gov.br/anvisa/pt-br" --source-org "ANVISA" --doc-type "site"
  bash scripts/smoke_pipeline.sh --url "https://www.gov.br/anvisa/pt-br"
TXT
}

URL=""
SOURCE_ORG="ANVISA"
DOC_TYPE="site"
RESET="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --url) URL="${2:-}"; shift 2;;
    --source-org) SOURCE_ORG="${2:-}"; shift 2;;
    --doc-type) DOC_TYPE="${2:-}"; shift 2;;
    --reset) RESET="true"; shift 1;;
    -h|--help) usage; exit 0;;
    *) echo "Argumento desconhecido: $1"; usage; exit 1;;
  esac
done

if [[ -z "$URL" ]]; then
  echo "ERRO: --url é obrigatório"
  usage
  exit 1
fi

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "ERRO: DATABASE_URL não definido."
  echo "Ex: export DATABASE_URL=\"postgresql://intelireg:intelireg@localhost:5555/intelireg\""
  exit 1
fi

command -v psql >/dev/null 2>&1 || { echo "ERRO: psql não encontrado."; exit 1; }
command -v python >/dev/null 2>&1 || { echo "ERRO: python não encontrado."; exit 1; }

export PYTHONPATH="src"

echo "== InteliReg Smoke Pipeline =="
echo "DATABASE_URL=$DATABASE_URL"
echo "URL=$URL"
echo "SOURCE_ORG=$SOURCE_ORG"
echo "DOC_TYPE=$DOC_TYPE"
echo "RESET=$RESET"
echo

if [[ "$RESET" == "true" ]]; then
  echo "[1/5] Resetando dados do MVP (TRUNCATE)..."
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -P pager=off -c \
    "TRUNCATE chunk_embeddings, embedding_chunks, nodes, document_versions, documents, jobs, rag_runs RESTART IDENTITY;"
  echo
fi

echo "[2/5] Ingestão (URL -> nodes -> document_version)..."
set +e
INGEST_OUT=$(python -m intelireg.cli.ingest_web --url "$URL" --source-org "$SOURCE_ORG" --doc-type "$DOC_TYPE" 2>&1)
INGEST_RC=$?
set -e
echo "$INGEST_OUT"
echo

if [[ $INGEST_RC -ne 0 ]]; then
  echo "ERRO: ingest_web retornou código $INGEST_RC"
  exit $INGEST_RC
fi

echo "[3/5] Garantindo que exista job queued (ou criando reindex job do último version_id)..."
QUEUED=$(psql "$DATABASE_URL" -t -P pager=off -c "SELECT COUNT(*) FROM jobs WHERE status='queued';" | xargs)
if [[ "${QUEUED:-0}" -eq 0 ]]; then
  VID=$(psql "$DATABASE_URL" -t -P pager=off -c "SELECT version_id FROM document_versions ORDER BY created_at DESC LIMIT 1;" | xargs)
  if [[ -z "$VID" ]]; then
    echo "ERRO: não achei version_id no banco após ingestão."
    exit 1
  fi

  echo "Nenhum job queued (provável dedup). Forçando READY_FOR_INDEX e enfileirando IndexVersionJob para version_id=$VID"
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -P pager=off -c \
    "UPDATE document_versions SET status='READY_FOR_INDEX' WHERE version_id='$VID';"

  python - <<PY
from intelireg.jobs import enqueue_job
from intelireg import settings
vid = "$VID"
job_id = enqueue_job("IndexVersionJob", {
    "version_id": vid,
    "pipeline_version": settings.PIPELINE_VERSION,
    "embedding_model_id": settings.EMBEDDING_MODEL_ID,
})
print("enqueued", job_id)
PY
else
  echo "Jobs queued encontrados: $QUEUED"
fi
echo

echo "[4/5] Rodando index_worker até esvaziar a fila (limite 20 iterações)..."
for i in $(seq 1 20); do
  QUEUED=$(psql "$DATABASE_URL" -t -P pager=off -c "SELECT COUNT(*) FROM jobs WHERE status='queued';" | xargs)
  if [[ "${QUEUED:-0}" -eq 0 ]]; then
    echo "Fila vazia."
    break
  fi
  echo "Iteração $i: queued=$QUEUED -> processando 1 job..."
  python -m intelireg.workers.index_worker --once || true
done
echo

echo "[5/5] Validações"
echo "---- jobs (últimos 5)"
psql "$DATABASE_URL" -P pager=off -c "SELECT job_id,type,status,attempts,left(coalesce(last_error,''),120) AS last_error FROM jobs ORDER BY job_id DESC LIMIT 5;"
echo

echo "---- versions (últimas 3)"
psql "$DATABASE_URL" -P pager=off -c "SELECT version_id,status,source_url,created_at FROM document_versions ORDER BY created_at DESC LIMIT 3;"
echo

echo "---- contagens"
psql "$DATABASE_URL" -P pager=off -c "SELECT COUNT(*) AS documents FROM documents;"
psql "$DATABASE_URL" -P pager=off -c "SELECT COUNT(*) AS versions FROM document_versions;"
psql "$DATABASE_URL" -P pager=off -c "SELECT COUNT(*) AS nodes FROM nodes;"
psql "$DATABASE_URL" -P pager=off -c "SELECT COUNT(*) AS chunks FROM embedding_chunks;"
psql "$DATABASE_URL" -P pager=off -c "SELECT COUNT(*) AS embeddings FROM chunk_embeddings;"
echo

echo "---- último chunk (preview)"
psql "$DATABASE_URL" -P pager=off -c "
SELECT chunk_id, version_id, pipeline_version, chunk_index, tokens_count,
       left(text, 200) || '...' AS text_preview,
       jsonb_array_length(node_refs) AS refs
FROM embedding_chunks
ORDER BY created_at DESC
LIMIT 1;"
echo

echo "---- último embedding (preview)"
psql "$DATABASE_URL" -P pager=off -c "
SELECT e.chunk_id, e.embedding_model_id, e.pipeline_version,
       left(e.embedding::text, 90) || '...' AS embedding_preview
FROM chunk_embeddings e
JOIN embedding_chunks c ON c.chunk_id = e.chunk_id
ORDER BY c.created_at DESC
LIMIT 1;"
echo

echo "OK: smoke pipeline finalizado."
