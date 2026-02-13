"""
Settings do MVP (sem .env por enquanto).

Regra:
- Parâmetros que mudam chunks/embeddings devem compor PIPELINE_VERSION e ir no payload do job.
- Worker deve usar o payload do job como fonte de verdade, com fallback para estes defaults.
"""
 
from __future__ import annotations

import os
from pathlib import Path

# Banco
DATABASE_URL_ENV = "DATABASE_URL"  # já usado pelo config.py

# Pipeline (index-level)
PIPELINE_VERSION = os.getenv("PIPELINE_VERSION", "mvp-v1")
EMBEDDING_MODEL_ID = os.getenv("EMBEDDING_MODEL_ID", "intfloat/multilingual-e5-small@384")

# Hugging Face / SentenceTransformers cache (evita re-download e acelera cold start)
HF_CACHE_DIR = os.getenv("HF_CACHE_DIR", str(Path("storage") / "hf_cache"))
Path(HF_CACHE_DIR).mkdir(parents=True, exist_ok=True)

# Canonicalização (ingestão)
CANON_MAX_HEADING_LEVEL = 3

# Chunking (indexação) - por enquanto em WORDS (proxy)
CHUNK_TARGET_WORDS = 450
CHUNK_MIN_WORDS = 200
CHUNK_MAX_WORDS = 650
CHUNK_OVERLAP_WORDS = 80

# Worker
INDEX_WORKER_ID_DEFAULT = "index-worker-1"
INDEX_WORKER_SLEEP_SECONDS = 5.0

# Retrieval (vamos usar depois no query_rag)
RETRIEVAL_N1 = int(os.getenv("RETRIEVAL_N1", "50"))
RETRIEVAL_N2 = int(os.getenv("RETRIEVAL_N2", "10"))  # etapa 7: vetor ON por padrão
RRF_K = int(os.getenv("RRF_K", "60"))
TOP_K_DEFAULT = int(os.getenv("TOP_K_DEFAULT", "12"))
HNSW_EF_SEARCH = int(os.getenv("HNSW_EF_SEARCH", "120"))
