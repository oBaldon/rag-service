#!/usr/bin/env bash
set -euo pipefail

# Hard reset (safe): recria APENAS o schema do app e reaplica o bootstrap/migrations
# Requer:
#   - DATABASE_URL definido
#   - scripts/bootstrap_db.sh existente e executável
# Opcional:
#   - --superuser-url ou env PG_SUPERUSER_URL para criar a extensão vector (pgvector), se necessário
#   - --schema ou env PG_SCHEMA (default: intelireg)

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -z "${DATABASE_URL:-}" || -z "${PG_SUPERUSER_URL:-}" || -z "${PG_SCHEMA:-}" ]]; then
  if [[ -f "$ROOT_DIR/.env" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$ROOT_DIR/.env"
    set +a
  fi
fi

: "${DATABASE_URL:?DATABASE_URL não definido. Ex: postgresql://intelireg:intelireg@localhost:5555/intelireg}"

YES=0
SUPERUSER_URL="${PG_SUPERUSER_URL:-}"
SCHEMA="${PG_SCHEMA:-intelireg}"
EXPECTED_DB="${PG_EXPECTED_DB:-intelireg}"
ALLOW_ANY_DB="${ALLOW_RESET_ANY_DB:-0}"

usage() {
  cat <<EOF
Uso:
  $0 [--yes] [--superuser-url <url>] [--schema <schema>]

Flags:
  --yes                 Pula confirmação interativa.
  --superuser-url <url> URL superuser (ex: postgres) para criar extensões (principalmente vector).
  --schema <schema>     Schema do app a ser resetado (default: intelireg).
  --allow-any-db        Permite resetar mesmo se o database não for o esperado.

Env:
  DATABASE_URL        Obrigatório.
  PG_SUPERUSER_URL    Opcional (alternativa ao --superuser-url).
  PG_SCHEMA           Opcional (alternativa ao --schema).
  PG_EXPECTED_DB      Opcional (default: intelireg). Nome do database esperado.
  ALLOW_RESET_ANY_DB  Opcional (0/1). Override do check do database.
EOF
}

# parse args
while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes)
      YES=1
      shift
      ;;
    --superuser-url)
      SUPERUSER_URL="${2:-}"
      [[ -n "${SUPERUSER_URL}" ]] || { echo "Erro: --superuser-url requer um valor."; exit 1; }
      shift 2
      ;;
    --schema)
      SCHEMA="${2:-}"
      [[ -n "${SCHEMA}" ]] || { echo "Erro: --schema requer um valor."; exit 1; }
      shift 2
      ;;
    --allow-any-db)
      ALLOW_ANY_DB=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Arg inválido: $1"
      usage
      exit 1
      ;;
  esac
done

# proteção: não permitir dropar public
if [[ "${SCHEMA}" == "public" ]]; then
  echo "ERRO: por segurança, este script não permite --schema public."
  echo "Use um schema dedicado (ex: intelireg) para evitar conflito com outros apps."
  exit 1
fi

# safety check: evita reset em database errado por engano
DB_ACTUAL="$(psql "$DATABASE_URL" -X -qAt -v ON_ERROR_STOP=1 -c "SELECT current_database();")"
USER_ACTUAL="$(psql "$DATABASE_URL" -X -qAt -v ON_ERROR_STOP=1 -c "SELECT current_user;")"
HOST_ACTUAL="$(psql "$DATABASE_URL" -X -qAt -v ON_ERROR_STOP=1 -c "SELECT coalesce(inet_server_addr()::text,'local');")"
PORT_ACTUAL="$(psql "$DATABASE_URL" -X -qAt -v ON_ERROR_STOP=1 -c "SELECT inet_server_port();")"

if [[ "${ALLOW_ANY_DB}" != "1" && "${DB_ACTUAL}" != "${EXPECTED_DB}" ]]; then
  echo "ERRO: safety-check bloqueou o reset."
  echo "  DATABASE_URL aponta para db='${DB_ACTUAL}' (user='${USER_ACTUAL}' host='${HOST_ACTUAL}' port='${PORT_ACTUAL}')"
  echo "  Mas o esperado é PG_EXPECTED_DB='${EXPECTED_DB}'."
  echo
  echo "Se você REALMENTE quer resetar esse database, rode com:"
  echo "  ALLOW_RESET_ANY_DB=1 ./scripts/reset_db.sh --yes"
  echo "ou:"
  echo "  ./scripts/reset_db.sh --yes --allow-any-db"
  exit 1
fi

if [[ "$YES" -ne 1 ]]; then
  echo "Isso vai RECRIAR o schema '${SCHEMA}' (DROP SCHEMA ${SCHEMA} CASCADE) e reaplicar o bootstrap."
  echo "⚠️  Isso apaga tudo que estiver no schema '${SCHEMA}' deste banco."
  echo
  read -r -p "Digite RESET para confirmar: " ans
  if [[ "$ans" != "RESET" ]]; then
    echo "Cancelado."
    exit 1
  fi
fi

BOOTSTRAP="./scripts/bootstrap_db.sh"
if [[ ! -f "$BOOTSTRAP" ]]; then
  echo "Erro: $BOOTSTRAP não encontrado."
  exit 1
fi
if [[ ! -x "$BOOTSTRAP" ]]; then
  echo "Aviso: $BOOTSTRAP não está executável. Tentando via bash."
fi

echo "[reset_db] DROP SCHEMA ${SCHEMA} CASCADE; CREATE SCHEMA ${SCHEMA};"
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -P pager=off <<SQL
BEGIN;
DROP SCHEMA IF EXISTS ${SCHEMA} CASCADE;
CREATE SCHEMA ${SCHEMA};
COMMIT;
SQL

echo "[reset_db] Reaplicando bootstrap/migrations..."
if [[ -n "$SUPERUSER_URL" ]]; then
  bash "$BOOTSTRAP" --db "$DATABASE_URL" --schema "$SCHEMA" --superuser-url "$SUPERUSER_URL"
else
  bash "$BOOTSTRAP" --db "$DATABASE_URL" --schema "$SCHEMA"
fi

echo "OK: schema '${SCHEMA}' recriado e bootstrap aplicado."