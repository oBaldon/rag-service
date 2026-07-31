from __future__ import annotations

import hmac

from fastapi import Header

from api.errors import ApiError
from intelireg import settings


def require_api_key(x_api_key: str = Header(default="")) -> None:
    expected = settings.RAG_API_KEY

    if settings.RAG_AUTH_REQUIRED and not expected:
        raise ApiError(
            status_code=503,
            code="rag_auth_misconfigured",
            message="A autenticação interna do serviço RAG não está configurada.",
        )

    if not expected and not settings.RAG_AUTH_REQUIRED:
        return

    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise ApiError(
            status_code=401,
            code="unauthorized",
            message="Credencial interna inválida ou ausente.",
        )
