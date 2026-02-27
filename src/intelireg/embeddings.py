from __future__ import annotations

from functools import lru_cache
from typing import List, Sequence
import os
import importlib
from intelireg import settings

class EmbeddingProviderError(RuntimeError):
    pass


@lru_cache(maxsize=4)
def _get_model(model_name: str):
    # Evita torch tentar inicializar CUDA em ambiente CPU e reduzir warnings.
    os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

    try:
        SentenceTransformer = importlib.import_module("sentence_transformers").SentenceTransformer
    except Exception as e:  # pragma: no cover
        raise EmbeddingProviderError(
            "sentence-transformers não está instalado. "
            "Instale com: pip install sentence-transformers"
        ) from e

    cache_folder = getattr(settings, "HF_CACHE_DIR", None)
    return SentenceTransformer(
        model_name,
        device="cpu",
        cache_folder=str(cache_folder) if cache_folder else None,
    )


def model_name_from_id(embedding_model_id: str) -> str:
    # aceita "intfloat/multilingual-e5-small@384" e retorna "intfloat/multilingual-e5-small"
    return (embedding_model_id or "").split("@", 1)[0].strip()


def embed_texts(
    texts: Sequence[str],
    *,
    model_name: str,
    role: str,
    batch_size: int = 32,
) -> List[List[float]]:
    if role not in ("query", "passage"):
        raise ValueError("role deve ser 'query' ou 'passage'")

    model = _get_model(model_name)

    prefix = "query: " if role == "query" else "passage: "
    prefixed = [prefix + (t or "") for t in texts]

    vecs = model.encode(
        prefixed,
        normalize_embeddings=True,
        batch_size=batch_size,
        show_progress_bar=False,
    )

    out: List[List[float]] = []
    for v in vecs:
        out.append(v.tolist() if hasattr(v, "tolist") else [float(x) for x in v])
    return out


def to_pgvector_literal(vec: Sequence[float]) -> str:
    return "[" + ",".join(f"{float(x):.6f}" for x in vec) + "]"


def embed_pgvector_literals(
    texts: Sequence[str],
    *,
    embedding_model_id: str,
    role: str,
    batch_size: int = 32,
) -> List[str]:
    """
    Gera embeddings E5 reais (query/passage) e retorna literais aceitos pelo pgvector.
    Mantém a mesma lógica já usada no MVP:
    - prefixo 'query: ' ou 'passage: '
    - normalize_embeddings=True
    - formatação com 6 casas decimais
    """
    model_name = model_name_from_id(embedding_model_id)
    vecs = embed_texts(texts, model_name=model_name, role=role, batch_size=batch_size)
    return [to_pgvector_literal(v) for v in vecs]


def embed_query_pgvector(question: str, embedding_model_id: str) -> str:
    """
    Compat: retorna embedding real da query (E5) como literal pgvector.
    """
    return embed_pgvector_literals(
        [question or ""],
        embedding_model_id=embedding_model_id,
        role="query",
        batch_size=1,
    )[0]
