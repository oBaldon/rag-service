from __future__ import annotations

import json

import pytest

from intelireg import settings
from intelireg.semantic_vocabulary import (
    build_passage_embedding_text,
    clear_semantic_vocabulary_cache,
    concept_governance_snapshot,
    expand_query,
    semantic_concept_coverage,
    vocabulary_summary,
    vocabulary_sources_snapshot,
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


def test_schema_v2_exposes_governance_without_changing_matching(tmp_path, monkeypatch):
    from intelireg.semantic_vocabulary import concept_governance_snapshot

    payload = {
        "schema_version": 2,
        "vocabulary_version": "governed-v1",
        "language": "pt-BR",
        "concepts": [
            {
                "id": "afe",
                "label": "AFE",
                "aliases": ["AFE"],
                "query_expansions": ["Autorização de Funcionamento"],
                "embedding_terms": ["AFE"],
                "domains": ["autorizacoes_empresas"],
                "regulatory_processes": ["afe"],
                "governance": {
                    "lifecycle": "active",
                    "review_status": "pending_domain_review",
                    "owner_role": "especialista_regulatorio",
                    "reviewer_role": "",
                    "reviewed_at": None,
                    "change_ref": "TEST-1",
                },
            }
        ],
    }
    path = tmp_path / "vocab-v2.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(settings, "SEMANTIC_VOCABULARY_ENABLED", True)
    monkeypatch.setattr(settings, "SEMANTIC_QUERY_EXPANSION_ENABLED", True)
    monkeypatch.setattr(settings, "SEMANTIC_VOCABULARY_PATH", str(path))
    monkeypatch.setattr(settings, "SEMANTIC_QUERY_EXPANSION_MAX_TERMS", 8)
    monkeypatch.setattr(settings, "SEMANTIC_QUERY_EXPANSION_MAX_CHARS", 1600)
    clear_semantic_vocabulary_cache()

    expansion = expand_query("O que é AFE?")
    summary = vocabulary_summary()
    governance = concept_governance_snapshot()

    assert expansion.matched_concepts == ("afe",)
    assert summary["schema_version"] == 2
    assert summary["pending_domain_review"] == 1
    assert governance[0]["regulatory_processes"] == ["afe"]
    assert governance[0]["change_ref"] == "TEST-1"

    clear_semantic_vocabulary_cache()


def test_schema_v2_rejects_alias_collision_between_active_concepts(tmp_path, monkeypatch):
    payload = {
        "schema_version": 2,
        "vocabulary_version": "bad-collision",
        "language": "pt-BR",
        "concepts": [
            {
                "id": "a",
                "label": "A",
                "aliases": ["Termo Único"],
                "domains": ["dominio_a"],
                "regulatory_processes": ["transversal"],
                "governance": {
                    "lifecycle": "active",
                    "review_status": "pending_domain_review",
                    "owner_role": "especialista",
                    "change_ref": "TEST-A",
                },
            },
            {
                "id": "b",
                "label": "B",
                "aliases": ["termo unico"],
                "domains": ["dominio_b"],
                "regulatory_processes": ["transversal"],
                "governance": {
                    "lifecycle": "active",
                    "review_status": "pending_domain_review",
                    "owner_role": "especialista",
                    "change_ref": "TEST-B",
                },
            },
        ],
    }
    path = tmp_path / "collision.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(settings, "SEMANTIC_VOCABULARY_ENABLED", True)
    monkeypatch.setattr(settings, "SEMANTIC_VOCABULARY_PATH", str(path))
    clear_semantic_vocabulary_cache()

    with pytest.raises(SemanticVocabularyError):
        vocabulary_summary()

    clear_semantic_vocabulary_cache()


def test_schema_v2_approved_concept_requires_reviewer_and_reviewed_at(tmp_path, monkeypatch):
    payload = {
        "schema_version": 2,
        "vocabulary_version": "bad-review",
        "language": "pt-BR",
        "concepts": [
            {
                "id": "afe",
                "label": "AFE",
                "aliases": ["AFE"],
                "domains": ["autorizacoes_empresas"],
                "regulatory_processes": ["afe"],
                "governance": {
                    "lifecycle": "active",
                    "review_status": "approved",
                    "owner_role": "especialista",
                    "change_ref": "TEST-A",
                },
            }
        ],
    }
    path = tmp_path / "bad-review.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(settings, "SEMANTIC_VOCABULARY_ENABLED", True)
    monkeypatch.setattr(settings, "SEMANTIC_VOCABULARY_PATH", str(path))
    clear_semantic_vocabulary_cache()

    with pytest.raises(SemanticVocabularyError):
        vocabulary_summary()

    clear_semantic_vocabulary_cache()


def test_schema_v3_exposes_priority_hierarchy_and_sources(tmp_path, monkeypatch):
    payload = {
        "schema_version": 3,
        "vocabulary_version": "governed-v3",
        "language": "pt-BR",
        "sources": [
            {
                "id": "ANVISA-TEST-01",
                "label": "Fonte oficial de teste",
                "url": "https://www.gov.br/anvisa/",
                "retrieved_at": "2026-08-20",
            }
        ],
        "concepts": [
            {
                "id": "parent",
                "label": "Conceito pai",
                "priority": "P1",
                "aliases": ["conceito pai"],
                "query_expansions": ["pai"],
                "embedding_terms": ["conceito pai"],
                "domains": ["teste"],
                "regulatory_processes": ["transversal"],
                "parent_concepts": [],
                "related_concepts": [],
                "source_refs": ["ANVISA-TEST-01"],
                "governance": {
                    "lifecycle": "active",
                    "review_status": "pending_domain_review",
                    "owner_role": "especialista",
                    "change_ref": "TEST-V3",
                },
            },
            {
                "id": "child",
                "label": "Conceito filho",
                "priority": "P0",
                "aliases": ["conceito filho", "filho regulatório"],
                "query_expansions": ["filho"],
                "embedding_terms": ["conceito filho"],
                "domains": ["teste"],
                "regulatory_processes": ["transversal"],
                "parent_concepts": ["parent"],
                "related_concepts": [],
                "source_refs": ["ANVISA-TEST-01"],
                "governance": {
                    "lifecycle": "active",
                    "review_status": "pending_domain_review",
                    "owner_role": "especialista",
                    "change_ref": "TEST-V3",
                },
            },
        ],
    }
    path = tmp_path / "vocab-v3.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(settings, "SEMANTIC_VOCABULARY_ENABLED", True)
    monkeypatch.setattr(settings, "SEMANTIC_QUERY_EXPANSION_ENABLED", True)
    monkeypatch.setattr(settings, "SEMANTIC_VOCABULARY_PATH", str(path))
    monkeypatch.setattr(settings, "SEMANTIC_QUERY_EXPANSION_MAX_TERMS", 8)
    monkeypatch.setattr(settings, "SEMANTIC_QUERY_EXPANSION_MAX_CHARS", 1600)
    clear_semantic_vocabulary_cache()

    summary = vocabulary_summary()
    governance = {item["id"]: item for item in concept_governance_snapshot()}
    sources = vocabulary_sources_snapshot()

    assert summary["schema_version"] == 3
    assert summary["priority_counts"] == {"P1": 1, "P0": 1}
    assert summary["source_count"] == 1
    assert summary["sourced_concepts"] == 2
    assert summary["hierarchy_edges"] == 1
    assert governance["child"]["priority"] == "P0"
    assert governance["child"]["parent_concepts"] == ["parent"]
    assert governance["child"]["source_refs"] == ["ANVISA-TEST-01"]
    assert sources[0]["id"] == "ANVISA-TEST-01"

    clear_semantic_vocabulary_cache()


def test_schema_v3_rejects_unknown_source_ref(tmp_path, monkeypatch):
    payload = {
        "schema_version": 3,
        "vocabulary_version": "bad-source-ref",
        "language": "pt-BR",
        "sources": [],
        "concepts": [
            {
                "id": "afe",
                "label": "AFE",
                "priority": "P0",
                "aliases": ["AFE"],
                "domains": ["autorizacoes_empresas"],
                "regulatory_processes": ["afe"],
                "parent_concepts": [],
                "related_concepts": [],
                "source_refs": ["ANVISA-MISSING"],
                "governance": {
                    "lifecycle": "active",
                    "review_status": "pending_domain_review",
                    "owner_role": "especialista",
                    "change_ref": "TEST",
                },
            }
        ],
    }
    path = tmp_path / "bad-source-ref.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(settings, "SEMANTIC_VOCABULARY_ENABLED", True)
    monkeypatch.setattr(settings, "SEMANTIC_VOCABULARY_PATH", str(path))
    clear_semantic_vocabulary_cache()

    with pytest.raises(SemanticVocabularyError):
        vocabulary_summary()

    clear_semantic_vocabulary_cache()


def test_schema_v3_rejects_parent_cycle(tmp_path, monkeypatch):
    source = {
        "id": "ANVISA-TEST-01",
        "label": "Fonte",
        "url": "https://www.gov.br/anvisa/",
        "retrieved_at": "2026-08-20",
    }
    base_governance = {
        "lifecycle": "active",
        "review_status": "pending_domain_review",
        "owner_role": "especialista",
        "change_ref": "TEST",
    }
    payload = {
        "schema_version": 3,
        "vocabulary_version": "bad-cycle",
        "language": "pt-BR",
        "sources": [source],
        "concepts": [
            {
                "id": "a",
                "label": "A",
                "priority": "P0",
                "aliases": ["termo a"],
                "domains": ["teste"],
                "regulatory_processes": ["transversal"],
                "parent_concepts": ["b"],
                "related_concepts": [],
                "source_refs": ["ANVISA-TEST-01"],
                "governance": base_governance,
            },
            {
                "id": "b",
                "label": "B",
                "priority": "P0",
                "aliases": ["termo b"],
                "domains": ["teste"],
                "regulatory_processes": ["transversal"],
                "parent_concepts": ["a"],
                "related_concepts": [],
                "source_refs": ["ANVISA-TEST-01"],
                "governance": base_governance,
            },
        ],
    }
    path = tmp_path / "cycle.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(settings, "SEMANTIC_VOCABULARY_ENABLED", True)
    monkeypatch.setattr(settings, "SEMANTIC_VOCABULARY_PATH", str(path))
    clear_semantic_vocabulary_cache()

    with pytest.raises(SemanticVocabularyError):
        vocabulary_summary()

    clear_semantic_vocabulary_cache()


def test_specific_alias_precedes_generic_parent_alias_in_schema_v3(
    tmp_path, monkeypatch
):
    payload = {
        "schema_version": 3,
        "vocabulary_version": "specificity-v3",
        "language": "pt-BR",
        "sources": [
            {
                "id": "ANVISA-TEST-01",
                "label": "Fonte",
                "url": "https://www.gov.br/anvisa/",
                "retrieved_at": "2026-08-20",
            }
        ],
        "concepts": [
            {
                "id": "generic",
                "label": "Método analítico",
                "priority": "P0",
                "aliases": ["método analítico"],
                "query_expansions": ["procedimento analítico"],
                "embedding_terms": ["método analítico"],
                "domains": ["qualidade"],
                "regulatory_processes": ["pos_registro"],
                "parent_concepts": [],
                "related_concepts": [],
                "source_refs": ["ANVISA-TEST-01"],
                "governance": {
                    "lifecycle": "active",
                    "review_status": "pending_domain_review",
                    "owner_role": "especialista",
                    "change_ref": "TEST",
                },
            },
            {
                "id": "specific",
                "label": "Mudança de método analítico",
                "priority": "P0",
                "aliases": ["mudança de método analítico"],
                "query_expansions": ["alteração pós-registro de método"],
                "embedding_terms": ["mudança de método analítico"],
                "domains": ["qualidade"],
                "regulatory_processes": ["pos_registro"],
                "parent_concepts": ["generic"],
                "related_concepts": [],
                "source_refs": ["ANVISA-TEST-01"],
                "governance": {
                    "lifecycle": "active",
                    "review_status": "pending_domain_review",
                    "owner_role": "especialista",
                    "change_ref": "TEST",
                },
            },
        ],
    }
    path = tmp_path / "specificity.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(settings, "SEMANTIC_VOCABULARY_ENABLED", True)
    monkeypatch.setattr(settings, "SEMANTIC_QUERY_EXPANSION_ENABLED", True)
    monkeypatch.setattr(settings, "SEMANTIC_VOCABULARY_PATH", str(path))
    monkeypatch.setattr(settings, "SEMANTIC_QUERY_EXPANSION_MAX_TERMS", 8)
    monkeypatch.setattr(settings, "SEMANTIC_QUERY_EXPANSION_MAX_CHARS", 1600)
    clear_semantic_vocabulary_cache()

    expansion = expand_query("Quais regras tratam de mudança de método analítico?")

    assert expansion.matched_concepts[:2] == ("specific", "generic")

    clear_semantic_vocabulary_cache()


def test_mvp_seed_cases_activate_expected_concepts(monkeypatch):
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    vocab_path = root / "config" / "semantic_vocabulary.json"
    cases_path = root / "golden" / "retrieval_mvp_seed_cases.json"

    monkeypatch.setattr(settings, "SEMANTIC_VOCABULARY_ENABLED", True)
    monkeypatch.setattr(settings, "SEMANTIC_QUERY_EXPANSION_ENABLED", True)
    monkeypatch.setattr(settings, "SEMANTIC_VOCABULARY_PATH", str(vocab_path))
    monkeypatch.setattr(settings, "SEMANTIC_QUERY_EXPANSION_MAX_TERMS", 8)
    monkeypatch.setattr(settings, "SEMANTIC_QUERY_EXPANSION_MAX_CHARS", 1600)
    clear_semantic_vocabulary_cache()

    payload = json.loads(cases_path.read_text(encoding="utf-8"))
    failures = []
    for case in payload["cases"]:
        expansion = expand_query(case["question"])
        missing = sorted(
            set(case.get("expected_concepts") or [])
            - set(expansion.matched_concepts)
        )
        if missing:
            failures.append(
                {
                    "id": case["id"],
                    "missing": missing,
                    "matched": list(expansion.matched_concepts),
                }
            )

    assert failures == []
    clear_semantic_vocabulary_cache()
