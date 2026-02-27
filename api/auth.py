from __future__ import annotations

import os
from fastapi import Header, HTTPException


def require_api_key(x_api_key: str = Header(default="")) -> None:
    expected = os.getenv("RAG_API_KEY", "").strip()
    # Se não configurar key, não bloqueia (útil p/ dev local).
    if not expected:
        return
    if x_api_key != expected:
        raise HTTPException(status_code=401, detail="unauthorized")