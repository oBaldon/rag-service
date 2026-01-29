"""
Settings do MVP (sem .env por enquanto).

Regra:
- Parâmetros que mudam chunks/embeddings devem compor PIPELINE_VERSION e ir no payload do job.
- Worker deve usar o payload do job como fonte de verdade, com fallback para estes defaults.
"""

# Banco
DATABASE_URL_ENV = "DATABASE_URL"  # já usado pelo config.py

# Pipeline (index-level)
PIPELINE_VERSION = "mvp-v1"
EMBEDDING_MODEL_ID = "text-embedding-3-small@1536"

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
RETRIEVAL_N1 = 50
RETRIEVAL_N2 = 50
RRF_K = 60
TOP_K_DEFAULT = 12
HNSW_EF_SEARCH = 120
