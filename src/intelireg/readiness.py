from __future__ import annotations

import logging
from typing import Any

from intelireg import settings
from intelireg.db import get_conn

logger = logging.getLogger(__name__)

_REQUIRED_TABLES = (
    "documents",
    "document_versions",
    "embedding_chunks",
    "chunk_embeddings",
    "rag_runs",
)


def check_readiness() -> tuple[bool, dict[str, dict[str, Any]]]:
    checks: dict[str, dict[str, Any]] = {}
    ready = True

    config_errors = settings.validate_runtime_configuration()
    if config_errors:
        ready = False
        checks["configuration"] = {
            "status": "not_ready",
            "detail": "; ".join(config_errors),
        }
    else:
        checks["configuration"] = {"status": "ready"}

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
                )
                vector_row = cur.fetchone()

                missing_tables: list[str] = []
                for table in _REQUIRED_TABLES:
                    qualified = f"{settings.PG_SCHEMA}.{table}"
                    cur.execute("SELECT to_regclass(%s)", (qualified,))
                    if cur.fetchone()[0] is None:
                        missing_tables.append(table)

        checks["database"] = {"status": "ready"}

        if vector_row is None:
            ready = False
            checks["pgvector"] = {
                "status": "not_ready",
                "detail": "extensão vector não encontrada",
            }
        else:
            checks["pgvector"] = {
                "status": "ready",
                "detail": str(vector_row[0]),
            }

        if missing_tables:
            ready = False
            checks["schema"] = {
                "status": "not_ready",
                "detail": "tabelas ausentes: " + ", ".join(missing_tables),
            }
        else:
            checks["schema"] = {"status": "ready"}

    except Exception:
        logger.exception("Falha no readiness do PostgreSQL")
        ready = False
        checks["database"] = {
            "status": "not_ready",
            "detail": "PostgreSQL indisponível",
        }
        checks.setdefault(
            "pgvector",
            {"status": "unknown", "detail": "não verificado"},
        )
        checks.setdefault(
            "schema",
            {"status": "unknown", "detail": "não verificado"},
        )

    checks["embedding"] = {
        "status": "ready" if settings.EMBEDDING_MODEL_ID else "not_ready",
        "detail": (
            f"{settings.EMBEDDING_MODEL_ID} "
            f"(dimensão {settings.EMBEDDING_DIMENSION})"
            if settings.EMBEDDING_MODEL_ID
            else "modelo não configurado"
        ),
    }
    if not settings.EMBEDDING_MODEL_ID:
        ready = False

    return ready, checks
