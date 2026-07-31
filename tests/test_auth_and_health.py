from __future__ import annotations

import pytest

from api.auth import require_api_key
from api.errors import ApiError
from intelireg import settings


def test_auth_is_fail_closed_when_required_without_key(monkeypatch):
    monkeypatch.setattr(settings, "RAG_AUTH_REQUIRED", True)
    monkeypatch.setattr(settings, "RAG_API_KEY", "")

    with pytest.raises(ApiError) as captured:
        require_api_key("")

    assert captured.value.status_code == 503
    assert captured.value.code == "rag_auth_misconfigured"


def test_auth_rejects_invalid_key(monkeypatch):
    monkeypatch.setattr(settings, "RAG_AUTH_REQUIRED", True)
    monkeypatch.setattr(settings, "RAG_API_KEY", "segredo")

    with pytest.raises(ApiError) as captured:
        require_api_key("incorreta")

    assert captured.value.status_code == 401
    assert captured.value.code == "unauthorized"


def test_auth_can_be_disabled_explicitly_for_local_tests(monkeypatch):
    monkeypatch.setattr(settings, "RAG_AUTH_REQUIRED", False)
    monkeypatch.setattr(settings, "RAG_API_KEY", "")

    assert require_api_key("") is None


def test_liveness_is_process_only(client):
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["service"] == "intelireg-rag"


def test_readiness_returns_200_when_dependencies_are_ready(
    client,
    monkeypatch,
):
    monkeypatch.setattr(
        "api.main.check_readiness",
        lambda: (
            True,
            {
                "database": {"status": "ready"},
                "schema": {"status": "ready"},
            },
        ),
    )

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_readiness_returns_503_when_dependency_is_unavailable(
    client,
    monkeypatch,
):
    monkeypatch.setattr(
        "api.main.check_readiness",
        lambda: (
            False,
            {
                "database": {
                    "status": "not_ready",
                    "detail": "PostgreSQL indisponível",
                }
            },
        ),
    )

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
