"""
Configuração central do serviço RAG.

Princípios:
- o servidor é a fonte de verdade para pipeline e modelo de embedding;
- parâmetros que alteram chunks/embeddings compõem PIPELINE_VERSION;
- autenticação é opcional apenas em ambientes local/test explicitamente configurados;
- limites de API evitam consultas acidentais ou maliciosas com custo desproporcional.
"""

from __future__ import annotations

import os
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default

    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False

    raise RuntimeError(
        f"{name} deve ser booleano (1/0, true/false, yes/no ou on/off)."
    )


# Ambiente e autenticação
APP_ENV = os.getenv("APP_ENV", "local").strip().lower()
RAG_API_KEY = os.getenv("RAG_API_KEY", "").strip()
RAG_AUTH_REQUIRED = _env_bool(
    "RAG_AUTH_REQUIRED",
    default=APP_ENV not in {"local", "test"},
)

# Banco
DATABASE_URL_ENV = "DATABASE_URL"
PG_SCHEMA = os.getenv("PG_SCHEMA", "intelireg").strip() or "intelireg"

# Pipeline e embedding: controlados pelo servidor
PIPELINE_VERSION = os.getenv("PIPELINE_VERSION", "mvp-v1").strip()
EMBEDDING_MODEL_ID = os.getenv(
    "EMBEDDING_MODEL_ID",
    "intfloat/multilingual-e5-small@384",
).strip()
EMBEDDING_DIMENSION = int(os.getenv("EMBEDDING_DIMENSION", "384"))

# Hugging Face / SentenceTransformers cache
HF_CACHE_DIR = os.getenv("HF_CACHE_DIR", str(Path("storage") / "hf_cache"))
Path(HF_CACHE_DIR).mkdir(parents=True, exist_ok=True)

# Canonicalização
CANON_MAX_HEADING_LEVEL = 3

# Coleta web: retries são reservados para timeout/rede, 429 e erros 5xx.
INGEST_HTTP_TIMEOUT_SECONDS = float(
    os.getenv("INGEST_HTTP_TIMEOUT_SECONDS", "30")
)
INGEST_HTTP_MAX_ATTEMPTS = int(os.getenv("INGEST_HTTP_MAX_ATTEMPTS", "3"))
INGEST_HTTP_BACKOFF_SECONDS = float(
    os.getenv("INGEST_HTTP_BACKOFF_SECONDS", "2")
)
INGEST_HTTP_MAX_BACKOFF_SECONDS = float(
    os.getenv("INGEST_HTTP_MAX_BACKOFF_SECONDS", "60")
)

# Chunking em palavras (proxy de tokens no MVP)
CHUNK_TARGET_WORDS = 450
CHUNK_MIN_WORDS = 200
CHUNK_MAX_WORDS = 650
CHUNK_OVERLAP_WORDS = 80

# Worker
INDEX_WORKER_ID_DEFAULT = "index-worker-1"
INDEX_WORKER_SLEEP_SECONDS = 5.0

# Retrieval
RETRIEVAL_N1 = int(os.getenv("RETRIEVAL_N1", "30"))
RETRIEVAL_N2 = int(os.getenv("RETRIEVAL_N2", "30"))
RRF_K = int(os.getenv("RRF_K", "60"))
TOP_K_DEFAULT = int(os.getenv("TOP_K_DEFAULT", "12"))
HNSW_EF_SEARCH = int(os.getenv("HNSW_EF_SEARCH", "120"))

# Retrieval regulatório / reranking
REGULATORY_IDENTIFIER_LOOKUP_ENABLED = _env_bool(
    "REGULATORY_IDENTIFIER_LOOKUP_ENABLED",
    default=True,
)
REGULATORY_IDENTIFIER_LOOKUP_LIMIT = int(
    os.getenv("REGULATORY_IDENTIFIER_LOOKUP_LIMIT", "250")
)
REGULATORY_IDENTIFIER_MAX_CHUNKS = int(
    os.getenv("REGULATORY_IDENTIFIER_MAX_CHUNKS", "1")
)
RERANK_ENABLED = _env_bool("RERANK_ENABLED", default=True)
RERANK_CANDIDATE_MULTIPLIER = int(
    os.getenv("RERANK_CANDIDATE_MULTIPLIER", "4")
)
RERANK_CANDIDATES_MAX = int(os.getenv("RERANK_CANDIDATES_MAX", "80"))
RERANK_LEXICAL_WEIGHT = float(os.getenv("RERANK_LEXICAL_WEIGHT", "0.012"))
RERANK_EXACT_IDENTIFIER_WEIGHT = float(
    os.getenv("RERANK_EXACT_IDENTIFIER_WEIGHT", "1.0")
)
# Diversificação fica disponível, mas desligada por padrão até o benchmark
# mostrar que o ganho supera a perda de múltiplos artigos úteis da mesma norma.
RERANK_DIVERSITY_ENABLED = _env_bool(
    "RERANK_DIVERSITY_ENABLED",
    default=False,
)
RERANK_MAX_CHUNKS_PER_DOCUMENT = int(
    os.getenv("RERANK_MAX_CHUNKS_PER_DOCUMENT", "3")
)

# Limites da API v1
QUESTION_MAX_LENGTH = int(os.getenv("QUESTION_MAX_LENGTH", "10000"))
RETRIEVAL_CANDIDATES_MAX = int(
    os.getenv("RETRIEVAL_CANDIDATES_MAX", "200")
)
RRF_K_MAX = int(os.getenv("RRF_K_MAX", "500"))
TOP_K_MAX = int(os.getenv("TOP_K_MAX", "50"))
REQUEST_ID_MAX_LENGTH = int(os.getenv("REQUEST_ID_MAX_LENGTH", "128"))


def validate_runtime_configuration() -> list[str]:
    """Retorna erros de configuração sem expor segredos."""

    errors: list[str] = []

    if not PIPELINE_VERSION:
        errors.append("PIPELINE_VERSION não configurado")

    if not EMBEDDING_MODEL_ID:
        errors.append("EMBEDDING_MODEL_ID não configurado")

    expected_suffix = f"@{EMBEDDING_DIMENSION}"
    if EMBEDDING_MODEL_ID and not EMBEDDING_MODEL_ID.endswith(expected_suffix):
        errors.append(
            "EMBEDDING_MODEL_ID incompatível com EMBEDDING_DIMENSION"
        )

    if RAG_AUTH_REQUIRED and not RAG_API_KEY:
        errors.append(
            "RAG_API_KEY é obrigatória quando RAG_AUTH_REQUIRED=true"
        )

    if QUESTION_MAX_LENGTH < 1:
        errors.append("QUESTION_MAX_LENGTH deve ser maior que zero")

    if RETRIEVAL_CANDIDATES_MAX < 1:
        errors.append("RETRIEVAL_CANDIDATES_MAX deve ser maior que zero")

    if RETRIEVAL_N1 <= 0 and RETRIEVAL_N2 <= 0:
        errors.append("Ao menos um canal de retrieval deve estar habilitado")

    if RETRIEVAL_N1 > RETRIEVAL_CANDIDATES_MAX:
        errors.append("RETRIEVAL_N1 excede RETRIEVAL_CANDIDATES_MAX")

    if RETRIEVAL_N2 > RETRIEVAL_CANDIDATES_MAX:
        errors.append("RETRIEVAL_N2 excede RETRIEVAL_CANDIDATES_MAX")

    if TOP_K_MAX < 1:
        errors.append("TOP_K_MAX deve ser maior que zero")

    if REGULATORY_IDENTIFIER_LOOKUP_LIMIT < 1:
        errors.append("REGULATORY_IDENTIFIER_LOOKUP_LIMIT deve ser maior que zero")

    if REGULATORY_IDENTIFIER_MAX_CHUNKS < 1:
        errors.append("REGULATORY_IDENTIFIER_MAX_CHUNKS deve ser maior que zero")

    if RERANK_CANDIDATE_MULTIPLIER < 1:
        errors.append("RERANK_CANDIDATE_MULTIPLIER deve ser maior que zero")

    if RERANK_CANDIDATES_MAX < 1:
        errors.append("RERANK_CANDIDATES_MAX deve ser maior que zero")

    if RERANK_LEXICAL_WEIGHT < 0:
        errors.append("RERANK_LEXICAL_WEIGHT não pode ser negativo")

    if RERANK_EXACT_IDENTIFIER_WEIGHT < 0:
        errors.append("RERANK_EXACT_IDENTIFIER_WEIGHT não pode ser negativo")

    if RERANK_MAX_CHUNKS_PER_DOCUMENT < 1:
        errors.append("RERANK_MAX_CHUNKS_PER_DOCUMENT deve ser maior que zero")

    return errors
