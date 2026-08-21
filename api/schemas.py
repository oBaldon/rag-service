from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from intelireg import settings


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )


class RetrievalRequest(StrictModel):
    question: str = Field(
        ...,
        min_length=1,
        max_length=settings.QUESTION_MAX_LENGTH,
    )
    version_id: Optional[UUID] = None

    # Mantidos por compatibilidade da API v1. Quando informados, devem
    # corresponder exatamente à configuração do servidor.
    pipeline_version: Optional[str] = Field(default=None, max_length=100)
    embedding_model_id: Optional[str] = Field(default=None, max_length=255)

    n1_fts: int = Field(default=settings.RETRIEVAL_N1, ge=0, le=settings.RETRIEVAL_CANDIDATES_MAX)
    n2_vec: int = Field(default=settings.RETRIEVAL_N2, ge=0, le=settings.RETRIEVAL_CANDIDATES_MAX)
    rrf_k: int = Field(default=60, ge=1, le=settings.RRF_K_MAX)
    top_k: int = Field(default=5, ge=1, le=settings.TOP_K_MAX)


class QueryRequest(RetrievalRequest):
    pass


class AskRequest(RetrievalRequest):
    n2_vec: int = Field(default=0, ge=0, le=settings.RETRIEVAL_CANDIDATES_MAX)


class RetrievalConfig(StrictModel):
    version_id: Optional[UUID]
    pipeline_version: str
    embedding_model_id: str
    n1_fts: int
    n2_vec: int
    rrf_k: int
    top_k: int
    strategy_version: Optional[str] = None
    candidate_limit: Optional[int] = None
    effective_n1_fts: Optional[int] = None
    effective_n2_vec: Optional[int] = None
    identifier: Optional[Dict[str, Any]] = None
    identifier_lookup_enabled: Optional[bool] = None
    semantic_expansion: Optional[Dict[str, Any]] = None
    semantic_passage_enrichment_enabled: Optional[bool] = None
    semantic_concept_lookup_enabled: Optional[bool] = None
    rerank_enabled: Optional[bool] = None
    diversity_enabled: Optional[bool] = None
    result_count: Optional[int] = None


class ScoreBreakdown(StrictModel):
    rrf_score: float
    fts_rank: Optional[int]
    fts_score: Optional[float]
    vec_rank: Optional[int]
    vec_distance: Optional[float]
    final_score: Optional[float] = None
    lexical_coverage: Optional[float] = None
    semantic_concept_coverage: Optional[float] = None
    semantic_vocabulary_score: Optional[float] = None
    semantic_concepts_matched: list[str] = Field(default_factory=list)
    semantic_lookup_match: bool = False
    semantic_lookup_rank: Optional[int] = None
    exact_identifier_match: bool = False
    exact_identifier_rank: Optional[int] = None


class ChunkEvidence(StrictModel):
    chunk_id: UUID
    version_id: UUID
    chunk_index: int
    tokens_count: int
    text: str


class DocumentMetadata(StrictModel):
    document_id: UUID
    title: str
    source_org: str
    doc_type: str
    source_url: str
    final_url: Optional[str]
    captured_at: Optional[datetime]


class QueryResult(StrictModel):
    rank: int
    rrf_score: float
    fts_rank: Optional[int]
    fts_score: Optional[float]
    vec_rank: Optional[int]
    vec_distance: Optional[float]
    scores: ScoreBreakdown
    chunk: ChunkEvidence
    document: DocumentMetadata
    citations: list[Any]


class QueryResponse(StrictModel):
    schema_version: Literal[1]
    run_type: Literal["query_rag"]
    request_id: str
    run_id: UUID
    query: str
    filters: Dict[str, Any]
    params: Dict[str, Any]
    retrieval: RetrievalConfig
    generated_at: datetime
    results: list[QueryResult]


class AskAnswer(StrictModel):
    text: str
    cited_sources: list[str]


class AskSource(StrictModel):
    source_id: str
    # Alias legado mantido durante a vigência do contrato v1.
    sid: str
    chunk_id: UUID
    version_id: UUID
    chunk_index: int
    text: str
    document: DocumentMetadata
    citations: list[Any]
    scores: ScoreBreakdown


class AskResponse(StrictModel):
    schema_version: Literal[1]
    run_type: Literal["ask_rag"]
    request_id: str
    run_id: UUID
    query: str
    filters: Dict[str, Any]
    params: Dict[str, Any]
    generated_at: datetime
    answer: AskAnswer
    sources: list[AskSource]


class LiveResponse(StrictModel):
    status: Literal["ok"]
    service: Literal["intelireg-rag"]
    pipeline_version: str


class CheckStatus(StrictModel):
    status: str
    detail: Optional[str] = None


class ReadyResponse(StrictModel):
    status: Literal["ready", "not_ready"]
    service: Literal["intelireg-rag"]
    checks: Dict[str, CheckStatus]


class ErrorBody(StrictModel):
    code: str
    message: str
    request_id: str
    details: Any | None = None


class ErrorResponse(StrictModel):
    error: ErrorBody
