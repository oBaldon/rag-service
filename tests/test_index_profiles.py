from __future__ import annotations

import pytest

from intelireg.index_profiles import (
    IndexProfile,
    IndexProfileMismatchError,
    ensure_index_profile,
)


class FakeCursor:
    def __init__(self, fetches):
        self.fetches = list(fetches)
        self.executed = []

    def execute(self, sql, params):
        self.executed.append((sql, tuple(params)))

    def fetchone(self):
        if not self.fetches:
            raise AssertionError("fetchone inesperado")
        return self.fetches.pop(0)


def test_index_profile_is_inserted_when_pipeline_is_new():
    cursor = FakeCursor(
        [
            None,   # index_profiles lookup
            (0,),   # existing embeddings
        ]
    )
    profile = IndexProfile(
        pipeline_version="semantic-v1",
        embedding_model_id="model@384",
        semantic_passage_enrichment=True,
        semantic_vocabulary_version="vocab-v1",
        semantic_embedding_profile_hash="abc",
    )

    ensure_index_profile(cursor, profile)

    assert any("INSERT INTO index_profiles" in sql for sql, _ in cursor.executed)


def test_index_profile_rejects_mixed_semantic_profiles():
    cursor = FakeCursor(
        [
            (True, "vocab-v1", "old-hash"),
        ]
    )
    profile = IndexProfile(
        pipeline_version="semantic-v1",
        embedding_model_id="model@384",
        semantic_passage_enrichment=True,
        semantic_vocabulary_version="vocab-v2",
        semantic_embedding_profile_hash="new-hash",
    )

    with pytest.raises(IndexProfileMismatchError):
        ensure_index_profile(cursor, profile)


def test_index_profile_refuses_adopting_untracked_existing_embeddings():
    cursor = FakeCursor(
        [
            None,
            (123,),
        ]
    )
    profile = IndexProfile(
        pipeline_version="mvp-v1",
        embedding_model_id="model@384",
        semantic_passage_enrichment=True,
        semantic_vocabulary_version="vocab-v1",
        semantic_embedding_profile_hash="abc",
    )

    with pytest.raises(IndexProfileMismatchError):
        ensure_index_profile(cursor, profile)
