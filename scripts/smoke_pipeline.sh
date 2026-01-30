#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'TXT'
Uso:
  scripts/smoke_pipeline.sh --url <URL> [--source-org ANVISA] [--doc-type site] [--reset]

O que faz:
  - (opcional) --reset: recria schema public + reaplica bootstrap (via scripts/reset_db.sh)
  - roda ingest_web (cria documents/document_versions/nodes e enfileira job)
  - drena a fila (jobs queued/failed com run_after <= now()) rodando index_worker --once
  - imprime validações e contagens

Pré-requisitos:
  - estar na raiz do repo (onde existe src/ e scripts/)
  - psql instalado
  - venv ativada (recomendado)
  - .env na raiz (opcional, mas recomendado) com DATABASE_URL/PG_SUPERUSER_URL/PYTHONPATH

Exemplos:
  bash scripts/smoke_pipeline.sh --reset --url "https://www.gov.br/anvisa/pt-br" --source-org "ANVISA" --doc-type "site"
  bash scripts/smoke_pipeline.sh --url "https://anvisalegis.datalegis.net/..."
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

# Carrega .env automaticamente (se existir)
if [[ -f ".env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source ".env"
  set +a
fi

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "ERRO: DATABASE_URL não definido."
  echo "Sugestão: crie .env na raiz com DATABASE_URL=..."
  exit 1
fi

# PYTHONPATH (se não vier do .env, assume src)
export PYTHONPATH="${PYTHONPATH:-src}"

command -v psql >/dev/null 2>&1 || { echo "ERRO: psql não encontrado."; exit 1; }

# python fallback
PYBIN="python"
if ! command -v "$PYBIN" >/dev/null 2>&1; then
  if command -v python3 >/dev/null 2>&1; then
    PYBIN="python3"
  else
    echo "ERRO: python/python3 não encontrado."
    exit 1
  fi
fi

echo "== InteliReg Smoke Pipeline =="
echo "DATABASE_URL=$DATABASE_URL"
echo "PG_SUPERUSER_URL=${PG_SUPERUSER_URL:-<não definido>}"
echo "PYTHONPATH=$PYTHONPATH"
echo "PYBIN=$PYBIN"
echo "URL=$URL"
echo "SOURCE_ORG=$SOURCE_ORG"
echo "DOC_TYPE=$DOC_TYPE"
echo "RESET=$RESET"
echo

if [[ "$RESET" == "true" ]]; then
  echo "[1/5] Resetando banco (DROP SCHEMA public + bootstrap)..."
  if [[ ! -f "./scripts/reset_db.sh" ]]; then
    echo "ERRO: ./scripts/reset_db.sh não encontrado."
    exit 1
  fi
  bash ./scripts/reset_db.sh --yes
  echo
else
  echo "[1/5] (skip) RESET=false"
  echo
fi

echo "[2/5] Ingestão (URL -> nodes -> document_version)..."
set +e
INGEST_OUT=$("$PYBIN" -m intelireg.cli.ingest_web --url "$URL" --source-org "$SOURCE_ORG" --doc-type "$DOC_TYPE" 2>&1)
INGEST_RC=$?
set -e
echo "$INGEST_OUT"
echo

if [[ $INGEST_RC -ne 0 ]]; then
  echo "ERRO: ingest_web retornou código $INGEST_RC"
  exit $INGEST_RC
fi

echo "[3/5] Garantindo que exista job pronto (queued/failed com run_after<=now)..."
READY=$(psql "$DATABASE_URL" -t -P pager=off -c "
  SELECT COUNT(*)
  FROM jobs
  WHERE status IN ('queued','failed')
    AND run_after <= NOW();
" | xargs)

if [[ "${READY:-0}" -eq 0 ]]; then
  VID=$(psql "$DATABASE_URL" -t -P pager=off -c "SELECT version_id FROM document_versions ORDER BY created_at DESC LIMIT 1;" | xargs)
  if [[ -z "$VID" ]]; then
    echo "ERRO: não achei version_id no banco após ingestão."
    exit 1
  fi

  echo "Nenhum job pronto. Re-enfileirando IndexVersionJob para version_id=$VID (pode ter sido dedup/run_after no futuro)."
  # força status da versão para permitir index
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -P pager=off -c \
    "UPDATE document_versions SET status='READY_FOR_INDEX' WHERE version_id='$VID';"

  # re-enfileira via código
  "$PYBIN" - <<PY
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
  echo "Jobs prontos para rodar agora: $READY"
fi
echo

echo "[4/5] Rodando index_worker até esvaziar a fila (limite 40 iterações)..."
for i in $(seq 1 40); do
  READY=$(psql "$DATABASE_URL" -t -P pager=off -c "
    SELECT COUNT(*)
    FROM jobs
    WHERE status IN ('queued','failed')
      AND run_after <= NOW();
  " | xargs)

  if [[ "${READY:-0}" -eq 0 ]]; then
    echo "Fila (pronta) vazia."
    break
  fi

  echo "Iteração $i: ready=$READY -> processando 1 job..."
  "$PYBIN" -m intelireg.workers.index_worker --once || true
done
echo

echo "[5/5] Validações"
echo "---- jobs (últimos 10)"
psql "$DATABASE_URL" -P pager=off -c \
  "SELECT job_id,type,status,attempts,run_after,left(coalesce(last_error,''),120) AS last_error
   FROM jobs ORDER BY job_id DESC LIMIT 10;"
echo

echo "---- versions (últimas 3)"
psql "$DATABASE_URL" -P pager=off -c \
  "SELECT version_id,status,source_url,created_at FROM document_versions ORDER BY created_at DESC LIMIT 3;"
echo

echo "---- contagens"
psql "$DATABASE_URL" -P pager=off -c "SELECT COUNT(*) AS documents FROM documents;"
psql "$DATABASE_URL" -P pager=off -c "SELECT COUNT(*) AS versions FROM document_versions;"
psql "$DATABASE_URL" -P pager=off -c "SELECT COUNT(*) AS nodes FROM nodes;"
psql "$DATABASE_URL" -P pager=off -c "SELECT COUNT(*) AS chunks FROM embedding_chunks;"
psql "$DATABASE_URL" -P pager=off -c "SELECT COUNT(*) AS embeddings FROM chunk_embeddings;"
echo

echo "---- top 5 chunks (maiores tokens_count)"
psql "$DATABASE_URL" -P pager=off -c "
SELECT chunk_index, tokens_count, length(text) AS chars, left(text, 120) || '...' AS preview
FROM embedding_chunks
ORDER BY tokens_count DESC
LIMIT 5;"
echo

echo "OK: smoke pipeline finalizado."
