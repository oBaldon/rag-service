#!/usr/bin/env bash
# Smoke operacional Docker: somente readiness; não executa consultas/auditoria.
set -euo pipefail
REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
if (($#)); then
    echo "Uso: $0 (verifica somente a API rag-api já iniciada)" >&2
    exit 2
fi
docker compose exec -T rag-api python docker/healthcheck.py
echo "OK: API RAG pronta (Docker; sem consulta ou alteração de dados)."
