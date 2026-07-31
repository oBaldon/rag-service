from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.main import app
from intelireg import settings


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(settings, "RAG_AUTH_REQUIRED", False)
    monkeypatch.setattr(settings, "RAG_API_KEY", "")
    return TestClient(app)
