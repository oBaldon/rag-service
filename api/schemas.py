from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Optional, Dict, Any


class QueryRequest(BaseModel):
    question: str = Field(..., min_length=1)
    version_id: Optional[str] = None
    pipeline_version: Optional[str] = None
    embedding_model_id: Optional[str] = None
    n1_fts: int = 30
    n2_vec: int = 30
    rrf_k: int = 60
    top_k: int = 5


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1)
    version_id: Optional[str] = None
    pipeline_version: Optional[str] = None
    embedding_model_id: Optional[str] = None
    n1_fts: int = 30
    n2_vec: int = 0
    rrf_k: int = 60
    top_k: int = 5