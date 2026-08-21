from __future__ import annotations

import logging
from typing import Any

from intelireg import settings
from intelireg.db import get_conn
from intelireg.semantic_vocabulary import load_semantic_vocabulary, SemanticVocabularyError
from intelireg.index_profiles import current_index_profile

logger = logging.getLogger(__name__)

_REQUIRED_TABLES = (
    "documents",
    "document_versions",
    "embedding_chunks",
    "chunk_embeddings",
    "index_profiles",
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

        if "index_profiles" not in missing_tables:
            try:
                expected_profile = current_index_profile(
                    pipeline_version=settings.PIPELINE_VERSION,
                    embedding_model_id=settings.EMBEDDING_MODEL_ID,
                )
            except SemanticVocabularyError:
                # A checagem dedicada do vocabulário abaixo reportará a causa.
                # Não devemos mascarar erro de configuração como indisponibilidade
                # do PostgreSQL.
                checks["index_profile"] = {
                    "status": "unknown",
                    "detail": "não verificado: vocabulário semântico inválido",
                }
            else:
                with get_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            SELECT
                              semantic_passage_enrichment,
                              semantic_vocabulary_version,
                              semantic_embedding_profile_hash
                            FROM index_profiles
                            WHERE pipeline_version = %s
                              AND embedding_model_id = %s
                            """,
                            (
                                settings.PIPELINE_VERSION,
                                settings.EMBEDDING_MODEL_ID,
                            ),
                        )
                        profile_row = cur.fetchone()

                if profile_row is None:
                    checks["index_profile"] = {
                        "status": "untracked",
                        "detail": (
                            "pipeline/modelo sem manifesto; permitido para índice "
                            "legado, mas novas indexações devem usar perfil registrado"
                        ),
                    }
                else:
                    profile_matches = (
                        bool(profile_row[0])
                        == expected_profile.semantic_passage_enrichment
                        and profile_row[2]
                        == expected_profile.semantic_embedding_profile_hash
                    )
                    if not profile_matches:
                        ready = False
                        checks["index_profile"] = {
                            "status": "not_ready",
                            "detail": (
                                "perfil semântico do pipeline diverge do vocabulário "
                                "carregado; use a versão de vocabulário correta ou "
                                "uma nova PIPELINE_VERSION"
                            ),
                        }
                    else:
                        checks["index_profile"] = {
                            "status": "ready",
                            "detail": (
                                f"pipeline={settings.PIPELINE_VERSION}; "
                                f"vocab={profile_row[1] or 'none'}"
                            ),
                        }

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
        checks.setdefault(
            "index_profile",
            {"status": "unknown", "detail": "não verificado"},
        )

    if settings.SEMANTIC_VOCABULARY_ENABLED:
        try:
            vocabulary = load_semantic_vocabulary()
            checks["semantic_vocabulary"] = {
                "status": "ready",
                "detail": (
                    f"{vocabulary.vocabulary_version}; "
                    f"{len(vocabulary.concepts)} conceitos; "
                    f"sha256={vocabulary.content_hash[:12]}"
                ),
            }
        except SemanticVocabularyError as exc:
            ready = False
            checks["semantic_vocabulary"] = {
                "status": "not_ready",
                "detail": str(exc),
            }
    else:
        checks["semantic_vocabulary"] = {
            "status": "disabled",
            "detail": "vocabulário semântico desabilitado",
        }

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
