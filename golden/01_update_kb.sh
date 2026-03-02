#!/usr/bin/env bash
set -euo pipefail

###############################################################################
# InteliReg MVP — Atualização da KB (reset → ingest → index) + checagens
# Baseado no run_intelireg_mvp.txt
#
# Objetivo (golden set):
# - Rodar a rotina "BD mágico" (atualização/refresh da KB) independente de query.
# - Ingerir dinamicamente N URLs definidas via arquivo/env (URL_*).
#
# RESTRIÇÃO: NÃO mexer nas seções 0 e 1 (mantidas como estão)
###############################################################################

# =========================================================
# 0) Carregar variáveis do .env (evita ter que passar inline)
# =========================================================
# ✅ Use isto UMA vez por terminal/sessão:
set -a
source .env
set +a

# =========================================================
# Schema do app (Opção A): sempre trabalhar no schema dedicado
# =========================================================
PG_SCHEMA="${PG_SCHEMA:-intelireg}"
PSQL_SEARCH_PATH="${PG_SCHEMA},public"

psql_app() {
  # garante search_path na mesma sessão do comando
  psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 -P pager=off \
    -c "SET search_path TO ${PSQL_SEARCH_PATH};" "$@"
}

# psql "scalar" (retorna 1 valor; NÃO imprime o 'SET' nem formatações)
# Uso: psql_scalar "SELECT count(*) FROM jobs WHERE status='queued';"
psql_scalar() {
  # -q  : quiet (suprime status do comando, incluindo "SET")
  # -tA : tuples-only + unaligned (imprime só o valor)
  psql "$DATABASE_URL" -X -qAt -v ON_ERROR_STOP=1 \
    -c "SET search_path TO ${PSQL_SEARCH_PATH}; $1"
}

# Checagens rápidas de ambiente
echo "[env] DATABASE_URL=$DATABASE_URL"
echo "[env] PG_SUPERUSER_URL=$PG_SUPERUSER_URL"
echo "[env] PYTHONPATH=$PYTHONPATH"
command -v psql >/dev/null && psql --version
command -v python >/dev/null && python --version

# =========================================================
# 1) RESET do banco (recria schema public + bootstrap)
# =========================================================
# Ajuste aqui se quiser:
#   - Para NÃO perguntar confirmação: use --yes
DO_RESET="${DO_RESET:-1}"
if [ "$DO_RESET" = "1" ]; then
  ./scripts/reset_db.sh --yes

  # (opcional) checar se extensões estão presentes
  psql_app -c "
  SELECT extname, extversion
  FROM pg_extension
  WHERE extname IN ('pgcrypto','unaccent','vector')
  ORDER BY extname;
  "

  # (opcional) checar se tabelas nasceram
  psql_app -c "\dt ${PG_SCHEMA}.*"
else
  echo "[1] DO_RESET=0 (pulando reset_db.sh)"
fi

# =========================================================
# 2) Garantir pgvector no sistema + criar extensão (se necessário)
# =========================================================
# Motivo: se /usr/share/postgresql/16/extension/vector.control não existir,
#         CREATE EXTENSION vector sempre falha com "not available".
echo "========================================================="
echo "[2] GARANTIR PGVECTOR"
echo "========================================================="

if ! psql "$DATABASE_URL" -X -P pager=off -qtAc "SELECT 1 FROM pg_extension WHERE extname='vector' LIMIT 1;" | grep -q 1; then
  if [ ! -f "/usr/share/postgresql/16/extension/vector.control" ]; then
    echo "[pgvector] vector.control ausente -> instalando postgresql-16-pgvector (sudo)..."
    sudo apt update
    sudo apt install -y postgresql-16-pgvector
    sudo systemctl restart postgresql || true
  fi

  echo "[pgvector] criando extensão vector via superuser..."
  psql "$PG_SUPERUSER_URL" -X -v ON_ERROR_STOP=1 -P pager=off -c "CREATE EXTENSION IF NOT EXISTS vector;"
fi

# Rechecar extensões
psql_app -c "
SELECT extname, extversion
FROM pg_extension
WHERE extname IN ('pgcrypto','unaccent','vector')
ORDER BY extname;
"

# =========================================================
# 3) Detectar comandos do ingest e do worker
# =========================================================
detect_python_entry() {
  local module="$1"
  local file="$2"

  if python -c "import $module" >/dev/null 2>&1; then
    echo "python -m $module"
    return 0
  fi
  if [ -f "$file" ]; then
    echo "python $file"
    return 0
  fi
  return 1
}

INGEST_CMD=""
WORKER_CMD=""

if INGEST_CMD="$(detect_python_entry intelireg.ingest_web src/intelireg/ingest_web.py)"; then :; else
  if INGEST_CMD="$(detect_python_entry intelireg.cli.ingest_web src/intelireg/cli/ingest_web.py)"; then :; else
    echo "ERRO: não achei ingest_web (módulo/arquivo). Ajuste os paths no script."
    exit 1
  fi
fi

if WORKER_CMD="$(detect_python_entry intelireg.index_worker src/intelireg/index_worker.py)"; then :; else
  if WORKER_CMD="$(detect_python_entry intelireg.workers.index_worker src/intelireg/workers/index_worker.py)"; then :; else
    echo "ERRO: não achei index_worker (módulo/arquivo). Ajuste os paths no script."
    exit 1
  fi
fi

echo "[detect] INGEST_CMD=$INGEST_CMD"
echo "[detect] WORKER_CMD=$WORKER_CMD"

# =========================================================
# 3.5) Carregar URLs (não hardcode; suporta N URLs via URL_*)
# =========================================================
URLS_FILE="${URLS_FILE:-golden/urls.env}"
if [ ! -f "$URLS_FILE" ]; then
  echo "ERRO: URLs file não encontrado: $URLS_FILE"
  echo "Crie $URLS_FILE com exports no padrão: export URL_XYZ='https://...'"
  exit 1
fi
# shellcheck disable=SC1090
source "$URLS_FILE"

# Descobrir todas as variáveis URL_* com valor definido
mapfile -t URL_VARS < <(compgen -v | grep -E '^URL_' | sort || true)

URLS=()
for v in "${URL_VARS[@]}"; do
  val="${!v:-}"
  if [ -n "$val" ]; then
    URLS+=("$val")
  fi
done

if [ "${#URLS[@]}" -eq 0 ]; then
  echo "ERRO: nenhuma variável URL_* encontrada com valor em $URLS_FILE"
  exit 1
fi

echo "[urls] carregadas ${#URLS[@]} URLs de $URLS_FILE"

# =========================================================
# 4) Ingest (URLs)
# =========================================================
DO_INGEST="${DO_INGEST:-1}"
if [ "$DO_INGEST" = "1" ]; then
  echo "========================================================="
  echo "[4] INGEST"
  echo "========================================================="

  REINDEX_EXISTING="${REINDEX_EXISTING:-0}"
  REINDEX_FLAG=""
  if [ "$REINDEX_EXISTING" = "1" ]; then
    REINDEX_FLAG="--reindex-existing"
  fi

  i=0
  for url in "${URLS[@]}"; do
    i=$((i+1))
    echo "[ingest] ($i/${#URLS[@]}) $url"
    $INGEST_CMD --url "$url" --source-org ANVISA --doc-type rdc $REINDEX_FLAG
  done

  psql_app -c "SELECT count(*) AS documents FROM documents;"
  psql_app -c "SELECT count(*) AS versions FROM document_versions;"
  psql_app -c "SELECT count(*) AS nodes FROM nodes;"
  psql_app -c "SELECT job_id, type, status, attempts, run_after FROM jobs ORDER BY job_id DESC LIMIT 10;"
else
  echo "[4] DO_INGEST=0 (pulando ingest)"
fi

# =========================================================
# 5) Index worker (até acabar fila)
# =========================================================
DO_INDEX="${DO_INDEX:-1}"
if [ "$DO_INDEX" = "1" ]; then
  echo "========================================================="
  echo "[5] INDEX WORKER"
  echo "========================================================="

  while true; do
    q="$(psql_scalar "SELECT count(*) FROM jobs WHERE status='queued';")"
    if [ "${q:-0}" = "0" ]; then
      break
    fi
    echo "[index] queued=$q -> processando 1 job"
    $WORKER_CMD --once
  done

  psql_app -c "SELECT version_id, status FROM document_versions ORDER BY captured_at DESC;"
  psql_app -c "SELECT count(*) AS chunks FROM embedding_chunks;"
  psql_app -c "SELECT count(*) AS embeddings FROM chunk_embeddings;"
  psql_app -c "SELECT job_id, type, status, attempts, last_error FROM jobs ORDER BY job_id DESC LIMIT 20;"
else
  echo "[5] DO_INDEX=0 (pulando index)"
fi

# =========================================================
# 6) Checagens (chunks + sintomas de headings colados)
# =========================================================
echo "========================================================="
echo "[6] CHECAGENS"
echo "========================================================="

psql_app -c "
SELECT chunk_index, tokens_count,
       left(text, 160) || '...' AS preview,
       jsonb_array_length(node_refs) AS refs
FROM embedding_chunks
ORDER BY chunk_index
LIMIT 10;
"

psql_app -c "
SELECT chunk_index, tokens_count, length(text) AS chars
FROM embedding_chunks
ORDER BY tokens_count DESC
LIMIT 10;
"

psql_app -c "
SELECT d.title, n.heading_text, n.path,
       right(n.text_normalized, 220) AS tail
FROM nodes n
JOIN document_versions v ON v.version_id=n.version_id
JOIN documents d ON d.document_id=v.document_id
WHERE n.heading_text LIKE 'Art.%'
  AND n.text_normalized ~* 'CAP[ÍI]TULO|Se[cç]ão|Subse[cç]ão|^ANEXO\\b'
ORDER BY d.title, n.heading_text
LIMIT 80;
"

psql_app -c "
SELECT d.title, length(n.text_normalized) AS chars,
       right(n.text_normalized, 260) AS tail
FROM nodes n
JOIN document_versions v ON v.version_id=n.version_id
JOIN documents d ON d.document_id=v.document_id
WHERE n.path='preambulo'
  AND n.text_normalized ~* 'CAP[ÍI]TULO'
ORDER BY chars DESC;
"

# =========================================================
# 7) Export (nodes/chunks) — opcional
# =========================================================
EXPORT_ALL="${EXPORT_ALL:-1}"
if [ "$EXPORT_ALL" = "1" ]; then
  OUT_NODES="storage/nodes_$(date +%Y%m%d_%H%M%S).jsonl"
  psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 -P pager=off -c "SET search_path TO ${PSQL_SEARCH_PATH};" <<SQL > "$OUT_NODES"
COPY (
  SELECT jsonb_build_object(
    'version_id', n.version_id,
    'document_id', v.document_id,
    'title', d.title,
    'source_org', d.source_org,
    'doc_type', d.doc_type,
    'node_id', n.node_id,
    'parent_id', n.parent_id,
    'node_index', n.node_index,
    'path', n.path,
    'heading_level', n.heading_level,
    'heading_text', n.heading_text,
    'chars', length(n.text_normalized),
    'text', replace(n.text_normalized, E'\n', '\\n')
  )::text
  FROM nodes n
  JOIN document_versions v ON v.version_id = n.version_id
  JOIN documents d ON d.document_id = v.document_id
  ORDER BY n.version_id, n.node_index
) TO STDOUT;
SQL
  gzip -9 "$OUT_NODES"
  echo "OK: ${OUT_NODES}.gz"

  OUT_CHUNKS="storage/chunks_$(date +%Y%m%d_%H%M%S).jsonl"
  psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 -P pager=off -c "SET search_path TO ${PSQL_SEARCH_PATH};" <<SQL > "$OUT_CHUNKS"
COPY (
  SELECT jsonb_build_object(
    'chunk_id', c.chunk_id,
    'version_id', c.version_id,
    'pipeline_version', c.pipeline_version,
    'chunk_index', c.chunk_index,
    'chunk_hash', c.chunk_hash,
    'tokens_count', c.tokens_count,
    'chars', length(c.text),
    'preview', left(c.text, 220),
    'node_refs', c.node_refs
  )::text
  FROM embedding_chunks c
  ORDER BY c.version_id, c.chunk_index
) TO STDOUT;
SQL
  gzip -9 "$OUT_CHUNKS"
  echo "OK: ${OUT_CHUNKS}.gz"
fi

echo "DONE ✅ (KB atualizada: reset=$DO_RESET ingest=$DO_INGEST index=$DO_INDEX export=$EXPORT_ALL urls=${#URLS[@]})"
