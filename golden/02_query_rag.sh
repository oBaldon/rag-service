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

# Checagens rápidas de ambiente
echo "[env] DATABASE_URL=$DATABASE_URL"
echo "[env] PG_SUPERUSER_URL=$PG_SUPERUSER_URL"
echo "[env] PYTHONPATH=$PYTHONPATH"
command -v psql >/dev/null && psql --version
command -v python >/dev/null && python --version

# =========================================================
# Pré-checagem: KB precisa estar indexada
# =========================================================
CHUNKS="$(psql "$DATABASE_URL" -P pager=off -t -c "SELECT count(*) FROM embedding_chunks;" | tr -d '[:space:]')"
EMBEDS="$(psql "$DATABASE_URL" -P pager=off -t -c "SELECT count(*) FROM chunk_embeddings;" | tr -d '[:space:]')"

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

# Mostrar cabeçalho do contrato v1 (rápido, útil em produção)
command -v jq >/dev/null && jq -r '{schema_version, run_type, retrieval, results_len:(.results|length)}' "$LATEST" || true

# Preview humano dos resultados (mantém o padrão do seu runbook)
command -v jq >/dev/null && jq -r '.results[] | {rank, fts_rank, vec_rank, preview:(.chunk.text|.[:160])}' "$LATEST" || true

# Saída final “pipe-friendly”: imprime só o caminho do JSON também
echo "$LATEST"
