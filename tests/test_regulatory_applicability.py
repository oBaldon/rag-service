from __future__ import annotations

import pytest

from intelireg.regulatory_applicability import (
    RegulatoryApplicabilityError,
    attach_regulatory_context,
    validate_import_payload,
)


DOC_A = "11111111-1111-1111-1111-111111111111"
DOC_B = "22222222-2222-2222-2222-222222222222"


def test_import_payload_requires_review_metadata_for_approved_status():
    payload = {
        "schema_version": 1,
        "batch_id": "batch-1",
        "status_assertions": [
            {
                "assertion_key": "status:a",
                "document_id": DOC_A,
                "status": "vigente",
                "asserted_by": "especialista_regulatorio",
                "review_status": "approved",
            }
        ],
        "relations": [],
    }

    with pytest.raises(RegulatoryApplicabilityError):
        validate_import_payload(payload)


def test_import_payload_accepts_reviewed_status_with_evidence():
    payload = {
        "schema_version": 1,
        "batch_id": "batch-1",
        "status_assertions": [
            {
                "assertion_key": "status:a",
                "document_id": DOC_A,
                "status": "vigente",
                "asserted_by": "especialista_regulatorio",
                "review_status": "approved",
                "reviewed_by": "revisor_regulatorio",
                "reviewed_at": "2026-08-20T20:00:00-03:00",
                "evidence_note": "Evidência curada; conteúdo de teste.",
            }
        ],
        "relations": [],
    }

    plan = validate_import_payload(payload)

    assert plan.status_assertions[0]["status"] == "vigente"
    assert plan.status_assertions[0]["review_status"] == "approved"


def test_import_payload_rejects_self_relation():
    payload = {
        "schema_version": 1,
        "batch_id": "batch-1",
        "status_assertions": [],
        "relations": [
            {
                "relation_key": "relation:a",
                "source_document_id": DOC_A,
                "target_document_id": DOC_A,
                "relation_type": "revoga",
                "asserted_by": "especialista_regulatorio",
            }
        ],
    }

    with pytest.raises(RegulatoryApplicabilityError):
        validate_import_payload(payload)


def test_attach_regulatory_context_preserves_ranking(monkeypatch):
    rows = [
        {
            "chunk_id": "c1",
            "final_score": 0.8,
            "document": {"document_id": DOC_A, "title": "A"},
        },
        {
            "chunk_id": "c2",
            "final_score": 0.7,
            "document": {"document_id": DOC_B, "title": "B"},
        },
    ]

    def fake_context(ids, *, max_relations_per_document):
        return {
            DOC_A: {
                "document_id": DOC_A,
                "status": {
                    "status": "vigente",
                    "basis": "approved_curated_assertion",
                    "assertion_id": None,
                    "assertion_key": "status:a",
                    "effective_from": None,
                    "valid_to": None,
                    "source_url": None,
                    "evidence_version_id": None,
                    "evidence_note": "teste",
                    "reviewed_by": "revisor",
                    "reviewed_at": "2026-08-20T20:00:00-03:00",
                },
                "relations": [],
            },
            DOC_B: {
                "document_id": DOC_B,
                "status": {
                    "status": "unknown",
                    "basis": "no_approved_curated_assertion",
                    "assertion_id": None,
                    "assertion_key": None,
                    "effective_from": None,
                    "valid_to": None,
                    "source_url": None,
                    "evidence_version_id": None,
                    "evidence_note": None,
                    "reviewed_by": None,
                    "reviewed_at": None,
                },
                "relations": [],
            },
        }

    monkeypatch.setattr(
        "intelireg.regulatory_applicability.load_regulatory_context",
        fake_context,
    )

    enriched = attach_regulatory_context(
        rows,
        enabled=True,
        max_relations_per_document=20,
    )

    assert [row["chunk_id"] for row in enriched] == ["c1", "c2"]
    assert enriched[0]["final_score"] == 0.8
    assert enriched[0]["document"]["regulatory_context"]["status"]["status"] == "vigente"
    assert enriched[1]["document"]["regulatory_context"]["status"]["status"] == "unknown"


def test_disabled_applicability_does_not_touch_rows():
    rows = [{"chunk_id": "c1", "document": {"document_id": DOC_A}}]
    assert attach_regulatory_context(rows, enabled=False) is rows
