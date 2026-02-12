from __future__ import annotations

from functools import lru_cache
from typing import List, Sequence

try:
    from sentence_transformers import SentenceTransformer
except Exception:  # pragma: no cover
    SentenceTransformer = None


class EmbeddingProviderError(RuntimeError):
    pass


@lru_cache(maxsize=4)
def _get_model(model_name: str) -> "SentenceTransformer":
    if SentenceTransformer is None:
        raise EmbeddingProviderError(
            "sentence-transformers não está instalado. "
            "Instale com: pip install sentence-transformers"
        )
    return SentenceTransformer(model_name, device="cpu")


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
