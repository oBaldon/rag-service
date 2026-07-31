from __future__ import annotations

from uuid import uuid4

import pytest

from intelireg.app import ask as ask_module
from intelireg.app import query as query_module


def test_run_query_persists_the_same_ids(monkeypatch: pytest.MonkeyPatch):
    captured: dict = {}
    request_id = "portal-req-1"
    run_id = str(uuid4())

    monkeypatch.setattr(
        query_module,
        "hybrid_retrieve_rrf",
        lambda **kwargs: [],
    )
    monkeypatch.setattr(
        query_module,
        "record_query_run",
        lambda **kwargs: captured.update(kwargs),
    )

    result = query_module.run_query(
        request_id=request_id,
        run_id=run_id,
        question="Pergunta",
        version_id=None,
        pipeline_version="mvp-v1",
        embedding_model_id="model@384",
        n1_fts=30,
        n2_vec=0,
        rrf_k=60,
        top_k=5,
        audit=True,
    )

    assert result["request_id"] == request_id
    assert result["run_id"] == run_id
    assert captured["request_id"] == request_id
    assert captured["run_id"] == run_id
    assert captured["result_json"]["run_id"] == run_id


def test_run_ask_uses_canonical_source_id_and_run_id(
    monkeypatch: pytest.MonkeyPatch,
):
    request_id = "portal-req-2"
    run_id = str(uuid4())
    persisted: dict = {}

    monkeypatch.setattr(
        ask_module,
        "hybrid_retrieve_rrf",
        lambda **kwargs: [
            {
                "chunk_id": str(uuid4()),
                "version_id": str(uuid4()),
                "chunk_index": 1,
                "text": "Trecho regulatório.",
                "document": {
                    "document_id": str(uuid4()),
                    "title": "Norma",
                    "source_org": "Fonte",
                    "doc_type": "norma",
                    "source_url": "https://example.test",
                    "final_url": None,
                    "captured_at": None,
                },
                "node_refs": [],
                "rrf_score": 0.03,
                "fts_rank": 1,
                "fts_score": 0.9,
                "vec_rank": None,
                "vec_distance": None,
            }
        ],
    )
    monkeypatch.setattr(
        ask_module,
        "extractive_answer",
        lambda question, sources: ("Resposta", ["S1"]),
    )

    def fake_insert(run):
        persisted.update(run)
        return run["run_id"]

    monkeypatch.setattr(ask_module, "insert_rag_run", fake_insert)

    result = ask_module.run_ask(
        request_id=request_id,
        run_id=run_id,
        question="Pergunta",
        version_id=None,
        pipeline_version="mvp-v1",
        embedding_model_id="model@384",
        n1_fts=30,
        n2_vec=0,
        rrf_k=60,
        top_k=5,
        audit=True,
    )

    assert result["request_id"] == request_id
    assert result["run_id"] == run_id
    assert result["sources"][0]["source_id"] == "S1"
    assert result["sources"][0]["sid"] == "S1"
    assert result["answer"]["cited_sources"] == ["S1"]
    assert persisted["run_id"] == run_id


def test_run_ask_fails_when_audit_does_not_preserve_run_id(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        ask_module,
        "hybrid_retrieve_rrf",
        lambda **kwargs: [],
    )
    monkeypatch.setattr(
        ask_module,
        "extractive_answer",
        lambda question, sources: ("", []),
    )
    monkeypatch.setattr(
        ask_module,
        "insert_rag_run",
        lambda run: str(uuid4()),
    )

    with pytest.raises(RuntimeError, match="run_id canônico"):
        ask_module.run_ask(
            request_id="request-1",
            run_id=str(uuid4()),
            question="Pergunta",
            version_id=None,
            pipeline_version="mvp-v1",
            embedding_model_id="model@384",
            n1_fts=30,
            n2_vec=0,
            rrf_k=60,
            top_k=5,
            audit=True,
        )
