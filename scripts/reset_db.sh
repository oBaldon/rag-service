#!/usr/bin/env bash
set -euo pipefail

# Hard reset: recria o schema public e reaplica o bootstrap/migrations
# Requer:
#   - DATABASE_URL definido
#   - scripts/bootstrap_db.sh existente e executável
# Opcional:
#   - --superuser-url ou env PG_SUPERUSER_URL para criar a extensão vector (pgvector), se necessário

: "${DATABASE_URL:?DATABASE_URL não definido. Ex: postgresql://intelireg:intelireg@localhost:5555/intelireg}"

YES=0
SUPERUSER_URL="${PG_SUPERUSER_URL:-}"

usage() {
  cat <<EOF
Uso:
  $0 [--yes] [--superuser-url <url>]

Flags:
  --yes                 Pula confirmação interativa.
  --superuser-url <url> URL superuser (ex: postgres) para criar extensões (principalmente vector).

Env:
  DATABASE_URL        Obrigatório.
  PG_SUPERUSER_URL    Opcional (alternativa ao --superuser-url).
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
      if [[ -z "${SUPERUSER_URL}" ]]; then
        echo "Erro: --superuser-url requer um valor."
        exit 1
      fi
      shift 2
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

if [[ "$YES" -ne 1 ]]; then
  echo "Isso vai RECRIAR o schema public (DROP SCHEMA public CASCADE) e reaplicar o bootstrap."
  echo "⚠️  Isso apaga tudo que estiver no schema public deste banco."
  echo
  read -r -p "Digite RESET para confirmar: " ans
  if [[ "$ans" != "RESET" ]]; then
    echo "Cancelado."
    exit 1
  fi
fi

# sanity check: bootstrap script existe
BOOTSTRAP="./scripts/bootstrap_db.sh"
if [[ ! -f "$BOOTSTRAP" ]]; then
  echo "Erro: $BOOTSTRAP não encontrado."
  echo "Crie o scripts/bootstrap_db.sh (ele deve aplicar extensões + migrations)."
  exit 1
fi
if [[ ! -x "$BOOTSTRAP" ]]; then
  echo "Aviso: $BOOTSTRAP não está executável. Tentando via bash."
fi

echo "[reset_db] DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -P pager=off <<'SQL'
BEGIN;
DROP SCHEMA IF EXISTS public CASCADE;
CREATE SCHEMA public;
COMMIT;
SQL

echo "[reset_db] Reaplicando bootstrap/migrations..."
if [[ -n "$SUPERUSER_URL" ]]; then
  bash "$BOOTSTRAP" --db "$DATABASE_URL" --superuser-url "$SUPERUSER_URL"
else
  bash "$BOOTSTRAP" --db "$DATABASE_URL"
fi

echo "OK: banco recriado e bootstrap aplicado."
