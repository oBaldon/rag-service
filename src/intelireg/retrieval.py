from __future__ import annotations

import hashlib
import random
from typing import Any, Dict, List, Optional

from intelireg.db import get_conn


def normalize_for_hash(s: str) -> str:
    return " ".join((s or "").split()).casefold()


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def fake_embedding_1536(seed_hex: str) -> str:
    """
    Placeholder determinístico para o MVP.
    Retorna string no formato aceito pelo pgvector: [0.1,0.2,...]
    """
    seed = int(seed_hex[:16], 16)
    rng = random.Random(seed)
    vals = [rng.uniform(-1.0, 1.0) for _ in range(1536)]
    return "[" + ",".join(f"{v:.6f}" for v in vals) + "]"


def embed_query_placeholder(question: str) -> str:
    """
    Embedding determinístico da pergunta (placeholder) para ser compatível com o MVP atual.
    No futuro: substituir por embeddings reais.
    """
    qh = sha256_hex("q|" + normalize_for_hash(question))
    return fake_embedding_1536(qh)


def hybrid_retrieve_rrf(
    question: str,
    pipeline_version: str,
    embedding_model_id: str,
    n1_fts: int,
    n2_vec: int,
    rrf_k: int,
    top_k: int,
    version_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Executa retrieval híbrido no Postgres:
    - FTS via tsvector (ts_rank_cd)
    - Vetorial via pgvector (<=>)
    - Combina via RRF (k = rrf_k)
    Retorna linhas já enriquecidas com metadados + texto + node_refs.
    """
    qvec = embed_query_placeholder(question)

    sql = """
    WITH
    q AS (
      SELECT websearch_to_tsquery('portuguese', %(question)s) AS q
    ),
    fts AS (
      SELECT
        c.chunk_id,
        row_number() OVER (ORDER BY ts_rank_cd(c.tsv, q.q) DESC) AS r_fts,
        ts_rank_cd(c.tsv, q.q) AS s_fts
      FROM embedding_chunks c
      JOIN document_versions v ON v.version_id = c.version_id
      CROSS JOIN q
      WHERE v.status = 'INDEXED'
        AND c.pipeline_version = %(pipeline_version)s
        AND ( %(version_id)s::uuid IS NULL OR c.version_id = %(version_id)s::uuid )
        AND c.tsv @@ q.q
      ORDER BY s_fts DESC
      LIMIT %(n1_fts)s
    ),
    vec AS (
      SELECT
        e.chunk_id,
        row_number() OVER (ORDER BY (e.embedding <=> %(qvec)s::vector) ASC) AS r_vec,
        (e.embedding <=> %(qvec)s::vector) AS d_vec
      FROM chunk_embeddings e
      JOIN embedding_chunks c ON c.chunk_id = e.chunk_id
      JOIN document_versions v ON v.version_id = c.version_id
      WHERE v.status = 'INDEXED'
        AND c.pipeline_version = %(pipeline_version)s
        AND e.pipeline_version = %(pipeline_version)s
        AND e.embedding_model_id = %(embedding_model_id)s
        AND ( %(version_id)s::uuid IS NULL OR c.version_id = %(version_id)s::uuid )
      ORDER BY d_vec ASC
      LIMIT %(n2_vec)s
    ),
    unioned AS (
      SELECT
        COALESCE(fts.chunk_id, vec.chunk_id) AS chunk_id,
        fts.r_fts, fts.s_fts,
        vec.r_vec, vec.d_vec
      FROM fts
      FULL OUTER JOIN vec USING (chunk_id)
    ),
    scored AS (
      SELECT
        u.*,
        (CASE WHEN u.r_fts IS NOT NULL THEN 1.0 / (%(rrf_k)s + u.r_fts) ELSE 0 END)
        +
        (CASE WHEN u.r_vec IS NOT NULL THEN 1.0 / (%(rrf_k)s + u.r_vec) ELSE 0 END)
        AS rrf_score
      FROM unioned u
    )
    SELECT
      s.chunk_id,
      s.rrf_score,
      s.r_fts,
      s.s_fts,
      s.r_vec,
      s.d_vec,

      c.version_id,
      c.pipeline_version,
      c.chunk_index,
      c.tokens_count,
      c.text,
      c.node_refs,

      d.document_id,
      d.title,
      d.source_org,
      d.doc_type,

      v.source_url,
      v.final_url,
      v.captured_at
    FROM scored s
    JOIN embedding_chunks c ON c.chunk_id = s.chunk_id
    JOIN document_versions v ON v.version_id = c.version_id
    JOIN documents d ON d.document_id = v.document_id
    ORDER BY s.rrf_score DESC
    LIMIT %(top_k)s;
    """

    params = {
        "question": question,
        "pipeline_version": pipeline_version,
        "embedding_model_id": embedding_model_id,
        "version_id": version_id,
        "n1_fts": n1_fts,
        "n2_vec": n2_vec,
        "rrf_k": rrf_k,
        "top_k": top_k,
        "qvec": qvec,
    }

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

    results: List[Dict[str, Any]] = []
    for r in rows:
        results.append(
            {
                "chunk_id": str(r[0]),
                "rrf_score": float(r[1]) if r[1] is not None else 0.0,
                "fts_rank": int(r[2]) if r[2] is not None else None,
                "fts_score": float(r[3]) if r[3] is not None else None,
                "vec_rank": int(r[4]) if r[4] is not None else None,
                "vec_distance": float(r[5]) if r[5] is not None else None,
                "version_id": str(r[6]),
                "pipeline_version": r[7],
                "chunk_index": r[8],
                "tokens_count": r[9],
                "text": r[10],
                "node_refs": r[11],
                "document": {
                    "document_id": str(r[12]),
                    "title": r[13],
                    "source_org": r[14],
                    "doc_type": r[15],
                    "source_url": r[16],
                    "final_url": r[17],
                    "captured_at": r[18].isoformat() if r[18] is not None else None,
                },
            }
        )

    return results
