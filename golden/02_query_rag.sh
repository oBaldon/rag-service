#!/usr/bin/env bash
set -euo pipefail

###############################################################################
# InteliReg MVP — Query síncrona (somente consulta)
# Baseado no run_intelireg_mvp.txt (seção 0 + seção 8), adaptado para:
# - receber a pergunta via argumento
# - não tocar no BD (sem reset/ingest/index)
###############################################################################

if [ "${1:-}" = "" ]; then
  echo "Uso: $0 \"<pergunta>\""
  echo "Ex:  $0 \"quais requisitos para produto de cannabis ter até 0,2% de THC?\""
  exit 2
fi

QUESTION="$1"
TOPK="${TOPK:-5}"

# =========================================================
# 0) Carregar variáveis do .env (evita ter que passar inline)
# =========================================================
set -a
source .env
set +a

PG_SCHEMA="${PG_SCHEMA:-intelireg}"
PSQL_SEARCH_PATH="${PG_SCHEMA},public"

psql_app() {
  psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 -P pager=off \
    -c "SET search_path TO ${PSQL_SEARCH_PATH};" "$@"
}

# psql "scalar" (retorna 1 valor; NÃO imprime o 'SET')
# Uso: psql_scalar "SELECT count(*) FROM embedding_chunks;"
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
# Pré-checagem: KB precisa estar indexada
# =========================================================
CHUNKS="$(psql_scalar "SELECT count(*) FROM embedding_chunks;")"
EMBEDS="$(psql_scalar "SELECT count(*) FROM chunk_embeddings;")"

echo "[kb] chunks=$CHUNKS embeddings=$EMBEDS"
if [ "${CHUNKS:-0}" = "0" ]; then
  echo "ERRO: embedding_chunks=0. Rode o 01_update_kb.sh antes (atualizar/gerar índice)."
  exit 1
fi
if [ "${EMBEDS:-0}" = "0" ]; then
  echo "ERRO: chunk_embeddings=0. Rode o 01_update_kb.sh antes (gerar embeddings)."
  exit 1
fi

# =========================================================
# Query RAG (gera arquivo JSON em storage/runs/)
# =========================================================
python -m intelireg.cli.query_rag \
  --q "$QUESTION" \
  --top-k "$TOPK"

LATEST="$(ls -t storage/runs/*_query.json | head -n 1)"
echo "[run] $LATEST"

# =========================================================
# Validação do contrato (schema v1) — mensagem amigável
# =========================================================
if command -v jq >/dev/null; then
  if ! jq -e '
    .schema_version == 1
    and .run_type == "query_rag"
    and (.retrieval | has("pipeline_version") and has("embedding_model_id") and has("n1_fts") and has("n2_vec") and has("rrf_k") and has("top_k"))
    and (.results | type=="array")
    and ((.retrieval.top_k | tonumber) == (.results | length))
    and (all(.results[]; has("rank") and has("scores") and has("chunk") and has("document") and has("citations")))
    and (all(.results[]; .scores | has("rrf_score") and has("fts_rank") and has("vec_rank")))
    and (all(.results[]; .chunk | has("chunk_id") and has("version_id") and has("chunk_index") and has("tokens_count") and has("text")))
  ' "$LATEST" >/dev/null; then
    echo "ERRO: o JSON gerado não está conforme o contrato schema v1." >&2
    echo "Arquivo: $LATEST" >&2
    echo "Referência: docs/schema_query_v1.md" >&2
    echo "Dica: rode o jq abaixo para inspecionar o cabeçalho:" >&2
    echo "  jq -r '{schema_version, run_type, retrieval, results_len:(.results|length)}' \"$LATEST\"" >&2
    exit 1
  fi
else
  echo "[warn] jq não encontrado; pulando validação automática do schema v1." >&2
fi

# Mostrar cabeçalho do contrato v1 (rápido, útil em produção)
command -v jq >/dev/null && jq -r '{schema_version, run_type, retrieval, results_len:(.results|length)}' "$LATEST" || true

# Preview humano dos resultados (mantém o padrão do seu runbook)
command -v jq >/dev/null && jq -r '.results[] | {rank, fts_rank, vec_rank, preview:(.chunk.text|.[:160])}' "$LATEST" || true

# Saída final “pipe-friendly”: imprime só o caminho do JSON também
echo "$LATEST"
