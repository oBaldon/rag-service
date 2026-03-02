#!/usr/bin/env bash
# scripts/bootstrap_db.sh
set -euo pipefail

usage() {
  cat <<'EOF'
Bootstrap do banco (migrations + extensões).

Uso:
  ./scripts/bootstrap_db.sh --db "$DATABASE_URL" --schema intelireg
  ./scripts/bootstrap_db.sh --db "$DATABASE_URL" --schema intelireg --superuser-url "postgresql://postgres:...@localhost:5555/intelireg"

Opções:
  --db URL                Connection string (default: $DATABASE_URL)
  --schema NAME           Schema do app (default: $PG_SCHEMA ou 'intelireg')
  --superuser-url URL     Connection string de superuser (default: $PG_SUPERUSER_URL)
                           Usado só para criar extensões que exigem superuser (ex: pgvector).
  --migrations-dir DIR    Diretório de migrations (default: db/migrations)
  -h, --help              Ajuda

Notas:
- Este script NÃO derruba schema/tabelas. Para limpar use ./scripts/reset_db.sh
- Garanta que suas migrations NÃO hardcodem "public." se você usar schema dedicado.
EOF
}

DB_URL="${DATABASE_URL:-}"
SUPER_URL="${PG_SUPERUSER_URL:-}"
MIG_DIR="db/migrations"
SCHEMA="${PG_SCHEMA:-intelireg}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --db) DB_URL="${2:-}"; shift 2 ;;
    --schema) SCHEMA="${2:-}"; shift 2 ;;
    --superuser-url) SUPER_URL="${2:-}"; shift 2 ;;
    --migrations-dir) MIG_DIR="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Argumento desconhecido: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ -z "${DB_URL}" ]]; then
  echo "ERRO: informe --db URL ou exporte DATABASE_URL" >&2
  usage
  exit 2
fi

if [[ -z "${SCHEMA}" ]]; then
  echo "ERRO: informe --schema NAME ou exporte PG_SCHEMA" >&2
  usage
  exit 2
fi

if ! command -v psql >/dev/null 2>&1; then
  echo "ERRO: 'psql' não encontrado. Instale o client do PostgreSQL." >&2
  exit 2
fi

if [[ ! -d "${MIG_DIR}" ]]; then
  echo "ERRO: diretório de migrations não encontrado: ${MIG_DIR}" >&2
  exit 2
fi

PSQL=(psql "$DB_URL" -X -v ON_ERROR_STOP=1 -P pager=off)
PSQL_SU=(psql "$SUPER_URL" -X -v ON_ERROR_STOP=1 -P pager=off)

echo "[bootstrap] testando conexão..."
"${PSQL[@]}" -qtAc "SELECT 1;" >/dev/null

SERVER_INFO="$("${PSQL[@]}" -qtAc "SELECT current_database()||'|'||current_user||'|'||coalesce(inet_server_addr()::text,'local')||'|'||inet_server_port();")"
DB_NAME="${SERVER_INFO%%|*}"
REST="${SERVER_INFO#*|}"
DB_USER="${REST%%|*}"
REST="${REST#*|}"
DB_HOST="${REST%%|*}"
DB_PORT="${REST##*|}"

echo "[bootstrap] conectado em db=${DB_NAME} user=${DB_USER} host=${DB_HOST} port=${DB_PORT} schema=${SCHEMA}"

ext_exists() {
  local ext="$1"
  "${PSQL[@]}" -qtAc "SELECT 1 FROM pg_extension WHERE extname='${ext}' LIMIT 1;" | grep -q 1
}

create_ext_as_owner() {
  local ext="$1"
  "${PSQL[@]}" -qtAc "CREATE EXTENSION IF NOT EXISTS ${ext};" >/dev/null
}

create_ext_as_superuser() {
  local ext="$1"
  [[ -n "${SUPER_URL}" ]] || return 1
  "${PSQL_SU[@]}" -qtAc "CREATE EXTENSION IF NOT EXISTS ${ext};" >/dev/null
}

ensure_extension() {
  local ext="$1"
  if ext_exists "$ext"; then
    echo "[bootstrap] extensão ok: ${ext}"
    return 0
  fi

  echo "[bootstrap] criando extensão: ${ext}"
  set +e
  create_ext_as_owner "$ext"
  local rc=$?
  set -e

  if [[ $rc -eq 0 ]]; then
    echo "[bootstrap] extensão criada: ${ext}"
    return 0
  fi

  if create_ext_as_superuser "$ext"; then
    echo "[bootstrap] extensão criada via superuser: ${ext}"
    return 0
  fi

  echo "ERRO: sem permissão para CREATE EXTENSION ${ext} (db=${DB_NAME}, role=${DB_USER})." >&2
  echo "Dica: rode UMA vez como superuser:" >&2
  echo "  psql -U postgres -p ${DB_PORT} -d ${DB_NAME} -c \"CREATE EXTENSION IF NOT EXISTS ${ext};\"" >&2
  if [[ -z "${SUPER_URL}" ]]; then
    echo "Ou passe --superuser-url / exporte PG_SUPERUSER_URL para o bootstrap criar automaticamente." >&2
  fi
  exit 3
}

# Extensões (ordem importa pouco, mas fica claro)
ensure_extension "pgcrypto"
ensure_extension "unaccent"
ensure_extension "vector"

# garante schema antes das migrations (search_path será setado por sessão ao aplicar cada arquivo)
echo "[bootstrap] garantindo schema..."
"${PSQL[@]}" -qtAc "CREATE SCHEMA IF NOT EXISTS ${SCHEMA};" >/dev/null

echo "[bootstrap] aplicando migrations em ${MIG_DIR}..."
mapfile -t MIGS < <(ls -1 "${MIG_DIR}"/*.sql 2>/dev/null | sort)
if [[ ${#MIGS[@]} -eq 0 ]]; then
  echo "ERRO: nenhuma migration .sql encontrada em ${MIG_DIR}" >&2
  exit 2
fi

# aplica migrations com search_path setado PARA ESTA SESSÃO (mesma conexão do -f)
for f in "${MIGS[@]}"; do
  echo "[bootstrap] -> $(basename "$f")"
  "${PSQL[@]}" \
    -v "app_schema=${SCHEMA}" \
    -c "SET search_path TO ${SCHEMA}, public;" \
    -f "$f" >/dev/null
done

echo "[bootstrap] checagens rápidas..."

# Ajuste as checagens para apontar para o schema configurado
"${PSQL[@]}" -qtAc "
  SELECT 1
  FROM information_schema.columns
  WHERE table_schema='${SCHEMA}' AND table_name='nodes'
    AND column_name='node_index'
    AND is_nullable='NO'
  LIMIT 1;
" | grep -q 1 || {
  echo "ERRO: nodes.node_index não está NOT NULL (verifique 0001_init.sql)" >&2
  exit 4
}

"${PSQL[@]}" -qtAc "
  SELECT 1
  FROM pg_constraint c
  JOIN pg_class t ON t.oid = c.conrelid
  JOIN pg_namespace n ON n.oid = t.relnamespace
  WHERE n.nspname='${SCHEMA}'
    AND t.relname='embedding_chunks'
    AND c.conname='uq_embedding_chunks_version_pipeline_hash'
    AND c.contype='u'
  LIMIT 1;
" | grep -q 1 || {
  echo "ERRO: constraint uq_embedding_chunks_version_pipeline_hash não existe (verifique init do embedding_chunks)" >&2
  exit 4
}

"${PSQL[@]}" -qtAc "
  SELECT 1
  FROM information_schema.columns
  WHERE table_schema='${SCHEMA}' AND table_name='chunk_embeddings'
    AND column_name='embedding'
    AND udt_name='vector'
  LIMIT 1;
" | grep -q 1 || {
  echo "ERRO: chunk_embeddings.embedding não é do tipo vector (verifique extensão pgvector / DDL)" >&2
  exit 4
}

echo "[bootstrap] OK ✅ banco pronto para uso."