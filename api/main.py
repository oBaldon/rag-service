from __future__ import annotations

from uuid import uuid4
from fastapi import FastAPI, Depends, Header, HTTPException
from fastapi.responses import JSONResponse

from intelireg import settings
from intelireg.app.query import run_query
from intelireg.app.ask import run_ask
from api.schemas import QueryRequest, AskRequest
from api.auth import require_api_key


app = FastAPI(title="InteliReg RAG Service", version="mvp-v1")


def _validate_retrieval_params(n1_fts: int, n2_vec: int) -> None:
    if n1_fts <= 0 and n2_vec <= 0:
        raise HTTPException(status_code=400, detail="at least one of n1_fts or n2_vec must be > 0")


@app.get("/health")
def health():
    return {
        "ok": True,
        "service": "intelireg-rag",
        "pipeline_version": getattr(settings, "PIPELINE_VERSION", "unknown"),
    }


@app.post("/v1/rag/query")
def rag_query(
    req: QueryRequest,
    _auth: None = Depends(require_api_key),
    x_request_id: str = Header(default=""),
):
    _validate_retrieval_params(req.n1_fts, req.n2_vec)

    request_id = x_request_id or str(uuid4())
    out = run_query(
        question=req.question,
        version_id=req.version_id,
        pipeline_version=req.pipeline_version or settings.PIPELINE_VERSION,
        embedding_model_id=req.embedding_model_id or settings.EMBEDDING_MODEL_ID,
        n1_fts=req.n1_fts,
        n2_vec=req.n2_vec,
        rrf_k=req.rrf_k,
        top_k=req.top_k,
        audit=True,
    )
    return JSONResponse(content=out, headers={"X-Request-Id": request_id})



@app.post("/v1/rag/ask")
def rag_ask(
    req: AskRequest,
    _auth: None = Depends(require_api_key),
    x_request_id: str = Header(default=""),
):
    _validate_retrieval_params(req.n1_fts, req.n2_vec)
    request_id = x_request_id or str(uuid4())
    out = run_ask(
        question=req.question,
        version_id=req.version_id,
        pipeline_version=req.pipeline_version or settings.PIPELINE_VERSION,
        embedding_model_id=req.embedding_model_id or settings.EMBEDDING_MODEL_ID,
        n1_fts=req.n1_fts,
        n2_vec=req.n2_vec,
        rrf_k=req.rrf_k,
        top_k=req.top_k,
        audit=True,
    )
    return JSONResponse(content=out, headers={"X-Request-Id": request_id})