from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from intelireg import settings


def _query_output(kwargs: dict) -> dict:
    document_id = str(uuid4())
    version_id = str(uuid4())
    chunk_id = str(uuid4())

    return {
        "schema_version": 1,
        "run_type": "query_rag",
        "request_id": kwargs["request_id"],
        "run_id": kwargs["run_id"],
        "query": kwargs["question"],
        "filters": {
            "version_id": kwargs["version_id"],
            "pipeline_version": kwargs["pipeline_version"],
            "embedding_model_id": kwargs["embedding_model_id"],
        },
        "params": {
            "n1_fts": kwargs["n1_fts"],
            "n2_vec": kwargs["n2_vec"],
            "rrf_k": kwargs["rrf_k"],
            "top_k": kwargs["top_k"],
        },
        "retrieval": {
            "version_id": kwargs["version_id"],
            "pipeline_version": kwargs["pipeline_version"],
            "embedding_model_id": kwargs["embedding_model_id"],
            "n1_fts": kwargs["n1_fts"],
            "n2_vec": kwargs["n2_vec"],
            "rrf_k": kwargs["rrf_k"],
            "top_k": kwargs["top_k"],
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "results": [
            {
                "rank": 1,
                "rrf_score": 0.03,
                "fts_rank": 1,
                "fts_score": 0.9,
                "vec_rank": 2,
                "vec_distance": 0.2,
                "scores": {
                    "rrf_score": 0.03,
                    "fts_rank": 1,
                    "fts_score": 0.9,
                    "vec_rank": 2,
                    "vec_distance": 0.2,
                },
                "chunk": {
                    "chunk_id": chunk_id,
                    "version_id": version_id,
                    "chunk_index": 0,
                    "tokens_count": 20,
                    "text": "Evidência de teste.",
                },
                "document": {
                    "document_id": document_id,
                    "title": "Documento de teste",
                    "source_org": "Fonte",
                    "doc_type": "norma",
                    "source_url": "https://example.test/original",
                    "final_url": "https://example.test/final",
                    "captured_at": datetime.now(timezone.utc).isoformat(),
                },
                "citations": [],
            }
        ],
    }


def test_query_uses_one_request_id_and_one_run_id(
    client,
    monkeypatch: pytest.MonkeyPatch,
):
    captured: dict = {}

    def fake_run_query(**kwargs):
        captured.update(kwargs)
        return _query_output(kwargs)

    monkeypatch.setattr("api.main.run_query", fake_run_query)

    response = client.post(
        "/v1/rag/query",
        headers={"X-Request-Id": "portal-analysis-123"},
        json={"question": "Qual é o requisito?"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["request_id"] == "portal-analysis-123"
    assert response.headers["X-Request-Id"] == "portal-analysis-123"
    assert captured["request_id"] == body["request_id"]
    assert captured["run_id"] == body["run_id"]
    UUID(body["run_id"])


def test_invalid_request_id_is_replaced(client, monkeypatch):
    monkeypatch.setattr(
        "api.main.run_query",
        lambda **kwargs: _query_output(kwargs),
    )

    response = client.post(
        "/v1/rag/query",
        headers={"X-Request-Id": "invalid request id"},
        json={"question": "Teste"},
    )

    assert response.status_code == 200
    assert response.json()["request_id"] != "invalid request id"
    UUID(response.json()["request_id"])


def test_both_retrieval_modes_disabled_returns_controlled_error(client):
    response = client.post(
        "/v1/rag/query",
        json={
            "question": "Teste",
            "n1_fts": 0,
            "n2_vec": 0,
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_retrieval_params"
    assert response.json()["error"]["request_id"]


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({"question": "Teste", "top_k": settings.TOP_K_MAX + 1}, "top_k"),
        (
            {
                "question": "Teste",
                "n1_fts": settings.RETRIEVAL_CANDIDATES_MAX + 1,
            },
            "n1_fts",
        ),
        ({"question": "x" * (settings.QUESTION_MAX_LENGTH + 1)}, "question"),
        ({"question": "Teste", "version_id": "não-é-uuid"}, "version_id"),
    ],
)
def test_request_limits_return_422(client, payload, field):
    response = client.post("/v1/rag/query", json=payload)

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "invalid_request"
    assert any(
        field in [str(item) for item in detail["location"]]
        for detail in body["error"]["details"]
    )


def test_server_rejects_arbitrary_embedding_model(client):
    response = client.post(
        "/v1/rag/query",
        json={
            "question": "Teste",
            "embedding_model_id": "outro/modelo@768",
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unsupported_embedding_model"


def test_server_accepts_its_configured_pipeline_and_model(
    client,
    monkeypatch,
):
    monkeypatch.setattr(
        "api.main.run_query",
        lambda **kwargs: _query_output(kwargs),
    )

    response = client.post(
        "/v1/rag/query",
        json={
            "question": "Teste",
            "pipeline_version": settings.PIPELINE_VERSION,
            "embedding_model_id": settings.EMBEDDING_MODEL_ID,
        },
    )

    assert response.status_code == 200
