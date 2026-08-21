from __future__ import annotations

from dataclasses import dataclass

from intelireg import settings
from intelireg.semantic_vocabulary import vocabulary_summary


class IndexProfileMismatchError(RuntimeError):
    pass


@dataclass(frozen=True)
class IndexProfile:
    pipeline_version: str
    embedding_model_id: str
    semantic_passage_enrichment: bool
    semantic_vocabulary_version: str | None
    semantic_embedding_profile_hash: str | None


def current_index_profile(
    *,
    pipeline_version: str,
    embedding_model_id: str,
) -> IndexProfile:
    if settings.SEMANTIC_VOCABULARY_ENABLED and settings.SEMANTIC_PASSAGE_ENRICHMENT_ENABLED:
        summary = vocabulary_summary()
        return IndexProfile(
            pipeline_version=pipeline_version,
            embedding_model_id=embedding_model_id,
            semantic_passage_enrichment=True,
            semantic_vocabulary_version=str(summary["vocabulary_version"]),
            semantic_embedding_profile_hash=str(summary["embedding_profile_hash"]),
        )

    return IndexProfile(
        pipeline_version=pipeline_version,
        embedding_model_id=embedding_model_id,
        semantic_passage_enrichment=False,
        semantic_vocabulary_version=None,
        semantic_embedding_profile_hash=None,
    )


def ensure_index_profile(cur, profile: IndexProfile) -> None:
    """
    Registra ou valida o perfil de embeddings de um pipeline.

    Se já existem embeddings sem manifesto para a mesma combinação
    pipeline/modelo, a função recusa adotar silenciosamente o perfil atual.
    Isso evita misturar vetores semanticamente incompatíveis.
    """
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
        (profile.pipeline_version, profile.embedding_model_id),
    )
    row = cur.fetchone()

    if row is not None:
        existing_enrichment = bool(row[0])
        existing_vocab_version = row[1]
        existing_profile_hash = row[2]

        if (
            existing_enrichment != profile.semantic_passage_enrichment
            or existing_profile_hash != profile.semantic_embedding_profile_hash
        ):
            raise IndexProfileMismatchError(
                "O pipeline/modelo já está associado a outro perfil semântico. "
                "Use uma nova PIPELINE_VERSION antes de reindexar. "
                f"pipeline={profile.pipeline_version!r} "
                f"modelo={profile.embedding_model_id!r} "
                f"vocab_existente={existing_vocab_version!r} "
                f"vocab_atual={profile.semantic_vocabulary_version!r}"
            )
        return

    cur.execute(
        """
        SELECT COUNT(*)
        FROM chunk_embeddings e
        JOIN embedding_chunks c ON c.chunk_id = e.chunk_id
        WHERE c.pipeline_version = %s
          AND e.pipeline_version = %s
          AND e.embedding_model_id = %s
        """,
        (
            profile.pipeline_version,
            profile.pipeline_version,
            profile.embedding_model_id,
        ),
    )
    existing_embeddings = int(cur.fetchone()[0])
    if existing_embeddings > 0:
        raise IndexProfileMismatchError(
            "Já existem embeddings para este pipeline/modelo sem manifesto de "
            "perfil semântico. Para preservar reprodutibilidade, use uma nova "
            "PIPELINE_VERSION em vez de sobrescrever o índice existente."
        )

    cur.execute(
        """
        INSERT INTO index_profiles (
          pipeline_version,
          embedding_model_id,
          semantic_passage_enrichment,
          semantic_vocabulary_version,
          semantic_embedding_profile_hash
        )
        VALUES (%s, %s, %s, %s, %s)
        """,
        (
            profile.pipeline_version,
            profile.embedding_model_id,
            profile.semantic_passage_enrichment,
            profile.semantic_vocabulary_version,
            profile.semantic_embedding_profile_hash,
        ),
    )
