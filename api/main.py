from __future__ import annotations

import logging
import re
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from api.auth import require_api_key
from api.errors import ApiError
from api.schemas import (
    AskRequest,
    AskResponse,
    ErrorResponse,
    LiveResponse,
    QueryRequest,
    QueryResponse,
    ReadyResponse,
)
from intelireg import settings
from intelireg.app.ask import run_ask
from intelireg.app.query import run_query
from intelireg.readiness import check_readiness

logger = logging.getLogger(__name__)
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]+$")

app = FastAPI(
    title="InteliReg RAG Service",
    version="1.0.0",
)


def _request_id(request: Request) -> str:
    value = getattr(request.state, "request_id", "")
    return value or str(uuid4())


def _error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    details: object | None = None,
) -> JSONResponse:
    body = {
        "error": {
            "code": code,
            "message": message,
            "request_id": _request_id(request),
        }
    }
    if details is not None:
        body["error"]["details"] = details

    return JSONResponse(
        status_code=status_code,
        content=body,
        headers={"X-Request-Id": _request_id(request)},
    )


@app.middleware("http")
async def request_context(request: Request, call_next):
    candidate = request.headers.get("X-Request-Id", "").strip()
    if (
        not candidate
        or len(candidate) > settings.REQUEST_ID_MAX_LENGTH
        or not _REQUEST_ID_RE.fullmatch(candidate)
    ):
        candidate = str(uuid4())

    request.state.request_id = candidate
    response = await call_next(request)
    response.headers["X-Request-Id"] = candidate
    return response


@app.exception_handler(ApiError)
async def handle_api_error(request: Request, exc: ApiError):
    return _error_response(
        request,
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        details=exc.details,
    )


@app.exception_handler(RequestValidationError)
async def handle_validation_error(
    request: Request,
    exc: RequestValidationError,
):
    details = [
        {
            "location": list(error.get("loc", [])),
            "message": error.get("msg", "valor inválido"),
            "type": error.get("type", "validation_error"),
        }
        for error in exc.errors()
    ]
    return _error_response(
        request,
        status_code=422,
        code="invalid_request",
        message="A requisição contém parâmetros inválidos.",
        details=details,
    )


@app.exception_handler(HTTPException)
async def handle_http_exception(request: Request, exc: HTTPException):
    message = (
        exc.detail
        if isinstance(exc.detail, str)
        else "A requisição não pôde ser processada."
    )
    return _error_response(
        request,
        status_code=exc.status_code,
        code="http_error",
        message=message,
    )


@app.exception_handler(Exception)
async def handle_unexpected_error(request: Request, exc: Exception):
    logger.exception(
        "Erro não tratado no serviço RAG",
        extra={"request_id": _request_id(request)},
    )
    return _error_response(
        request,
        status_code=500,
        code="internal_error",
        message="O serviço RAG encontrou um erro interno.",
    )


def _validate_retrieval_params(n1_fts: int, n2_vec: int) -> None:
    if n1_fts <= 0 and n2_vec <= 0:
        raise ApiError(
            status_code=400,
            code="invalid_retrieval_params",
            message="Ao menos um entre n1_fts e n2_vec deve ser maior que zero.",
        )


def _server_pipeline(requested: str | None) -> str:
    if requested and requested != settings.PIPELINE_VERSION:
        raise ApiError(
            status_code=400,
            code="unsupported_pipeline_version",
            message="A versão de pipeline solicitada não está disponível.",
        )
    return settings.PIPELINE_VERSION


def _server_embedding_model(requested: str | None) -> str:
    if requested and requested != settings.EMBEDDING_MODEL_ID:
        raise ApiError(
            status_code=400,
            code="unsupported_embedding_model",
            message="O modelo de embedding solicitado não está disponível.",
        )
    return settings.EMBEDDING_MODEL_ID


@app.get("/health", response_model=LiveResponse)
@app.get("/health/live", response_model=LiveResponse)
def live():
    return {
        "status": "ok",
        "service": "intelireg-rag",
        "pipeline_version": settings.PIPELINE_VERSION,
    }


@app.get(
    "/health/ready",
    response_model=ReadyResponse,
    responses={503: {"model": ReadyResponse}},
)
def ready():
    is_ready, checks = check_readiness()
    content = {
        "status": "ready" if is_ready else "not_ready",
        "service": "intelireg-rag",
        "checks": checks,
    }
    if not is_ready:
        return JSONResponse(status_code=503, content=content)
    return content


@app.post(
    "/v1/rag/query",
    response_model=QueryResponse,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
def rag_query(
    req: QueryRequest,
    request: Request,
    _auth: None = Depends(require_api_key),
):
    _validate_retrieval_params(req.n1_fts, req.n2_vec)

    request_id = _request_id(request)
    run_id = str(uuid4())

    try:
        return run_query(
            request_id=request_id,
            run_id=run_id,
            question=req.question,
            version_id=str(req.version_id) if req.version_id else None,
            pipeline_version=_server_pipeline(req.pipeline_version),
            embedding_model_id=_server_embedding_model(
                req.embedding_model_id
            ),
            n1_fts=req.n1_fts,
            n2_vec=req.n2_vec,
            rrf_k=req.rrf_k,
            top_k=req.top_k,
            audit=True,
        )
    except ApiError:
        raise
    except Exception as exc:
        logger.exception(
            "Falha na consulta RAG",
            extra={"request_id": request_id, "run_id": run_id},
        )
        raise ApiError(
            status_code=503,
            code="rag_query_failed",
            message="A recuperação de evidências está temporariamente indisponível.",
        ) from exc


@app.post(
    "/v1/rag/ask",
    response_model=AskResponse,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
def rag_ask(
    req: AskRequest,
    request: Request,
    _auth: None = Depends(require_api_key),
):
    _validate_retrieval_params(req.n1_fts, req.n2_vec)

    request_id = _request_id(request)
    run_id = str(uuid4())

    try:
        return run_ask(
            request_id=request_id,
            run_id=run_id,
            question=req.question,
            version_id=str(req.version_id) if req.version_id else None,
            pipeline_version=_server_pipeline(req.pipeline_version),
            embedding_model_id=_server_embedding_model(
                req.embedding_model_id
            ),
            n1_fts=req.n1_fts,
            n2_vec=req.n2_vec,
            rrf_k=req.rrf_k,
            top_k=req.top_k,
            audit=True,
        )
    except ApiError:
        raise
    except Exception as exc:
        logger.exception(
            "Falha na resposta extrativa RAG",
            extra={"request_id": request_id, "run_id": run_id},
        )
        raise ApiError(
            status_code=503,
            code="rag_ask_failed",
            message="A resposta extrativa está temporariamente indisponível.",
        ) from exc
