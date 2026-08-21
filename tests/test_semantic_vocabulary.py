from __future__ import annotations

import json

import pytest

from intelireg import settings
from intelireg.semantic_vocabulary import (
    build_passage_embedding_text,
    clear_semantic_vocabulary_cache,
    expand_query,
    semantic_concept_coverage,
    vocabulary_summary,
    SemanticVocabularyError,
)


@pytest.fixture
def semantic_vocab_path(tmp_path, monkeypatch):
    payload = {
        "schema_version": 1,
        "vocabulary_version": "test-v1",
        "language": "pt-BR",
        "concepts": [
            {
                "id": "ich",
                "label": "ICH",
                "enabled": True,
                "aliases": [
                    "ICH",
                    "harmonização farmacêutica",
                ],
                "query_expansions": [
                    "ICH",
                    "International Council for Harmonisation",
                ],
                "embedding_terms": [
                    "ICH",
                    "harmonização farmacêutica internacional",
                ],
            }
        ],
    }
    path = tmp_path / "vocab.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(settings, "SEMANTIC_VOCABULARY_ENABLED", True)
    monkeypatch.setattr(settings, "SEMANTIC_QUERY_EXPANSION_ENABLED", True)
    monkeypatch.setattr(settings, "SEMANTIC_VOCABULARY_PATH", str(path))
    monkeypatch.setattr(settings, "SEMANTIC_QUERY_EXPANSION_MAX_TERMS", 8)
    monkeypatch.setattr(settings, "SEMANTIC_QUERY_EXPANSION_MAX_CHARS", 1600)
    monkeypatch.setattr(settings, "SEMANTIC_PASSAGE_ENRICHMENT_ENABLED", True)
    monkeypatch.setattr(settings, "SEMANTIC_PASSAGE_MAX_TERMS", 6)
    monkeypatch.setattr(settings, "SEMANTIC_PASSAGE_MAX_PREFIX_CHARS", 700)
    monkeypatch.setattr(settings, "SEMANTIC_CONCEPT_LOOKUP_MAX_TERMS", 12)
    clear_semantic_vocabulary_cache()
    yield path
    clear_semantic_vocabulary_cache()


def test_query_expansion_maps_regulatory_paraphrase_to_ich(semantic_vocab_path):
    expansion = expand_query(
        "atribuições de representantes brasileiros em fóruns internacionais "
        "de harmonização farmacêutica"
    )

    assert expansion.applied is True
    assert expansion.matched_concepts == ("ich",)
    assert "ICH" in expansion.added_terms
    assert "International Council for Harmonisation" in expansion.expanded_query


def test_query_expansion_is_auditable(semantic_vocab_path):
    expansion = expand_query("harmonização farmacêutica")
    debug = expansion.debug_dict()

    assert debug["vocabulary_version"] == "test-v1"
    assert len(debug["vocabulary_hash"]) == 64
    assert debug["matched_concepts"] == ["ich"]
    assert debug["expanded_query"]


def test_passage_enrichment_does_not_replace_canonical_chunk(semantic_vocab_path):
    canonical = "Art. 12 São responsabilidades dos Representantes da Anvisa na Assembleia ICH."
    enriched, concepts = build_passage_embedding_text(
        title="Portaria 1.520/2019",
        doc_type="prt",
        chunk_text=canonical,
    )

    assert concepts == ("ich",)
    assert canonical in enriched
    assert enriched != canonical
    assert "Documento: Portaria 1.520/2019" in enriched
    assert "Conceitos: ICH" in enriched


def test_semantic_concept_coverage_connects_aliases(semantic_vocab_path):
    coverage, matched = semantic_concept_coverage(
        ("ich",),
        "Representantes da Anvisa na Assembleia ICH e Comitê Gestor.",
    )

    assert coverage == 1.0
    assert matched == ("ich",)


def test_vocabulary_summary_exposes_version_and_hash(semantic_vocab_path):
    summary = vocabulary_summary()

    assert summary["vocabulary_version"] == "test-v1"
    assert summary["concept_count"] == 1
    assert summary["concept_ids"] == ["ich"]
    assert len(summary["content_hash"]) == 64


def test_concept_detection_remains_available_when_query_expansion_is_disabled(
    semantic_vocab_path,
    monkeypatch,
):
    monkeypatch.setattr(settings, "SEMANTIC_QUERY_EXPANSION_ENABLED", False)

    expansion = expand_query("fóruns de harmonização farmacêutica")

    assert expansion.applied is False
    assert expansion.expanded_query == "fóruns de harmonização farmacêutica"
    assert expansion.matched_concepts == ("ich",)
    assert expansion.added_terms == ()


def test_vocabulary_rejects_unknown_concept_field(tmp_path, monkeypatch):
    payload = {
        "schema_version": 1,
        "vocabulary_version": "bad-v1",
        "language": "pt-BR",
        "concepts": [
            {
                "id": "ich",
                "label": "ICH",
                "aliases": ["ICH"],
                "query_expansion_typo": ["foo"],
            }
        ],
    }
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(settings, "SEMANTIC_VOCABULARY_ENABLED", True)
    monkeypatch.setattr(settings, "SEMANTIC_VOCABULARY_PATH", str(path))
    clear_semantic_vocabulary_cache()

    with pytest.raises(SemanticVocabularyError):
        vocabulary_summary()

    clear_semantic_vocabulary_cache()
