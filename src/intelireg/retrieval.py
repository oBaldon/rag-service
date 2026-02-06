from __future__ import annotations

import hashlib
import random
import re
from typing import Any, Dict, List, Optional

from intelireg.db import get_conn

_TOKEN_RE = re.compile(r"[a-zA-ZÀ-ÿ0-9]+", re.UNICODE)


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


_FTS_STOPWORDS = {
    # PT-BR comuns (MVP): removemos termos "conversacionais" que matam o AND do FTS
    "quais", "qual", "quais", "que", "o", "a", "os", "as",
    "um", "uma", "uns", "umas",
    "de", "do", "da", "dos", "das",
    "para", "por", "com", "sem", "em", "no", "na", "nos", "nas",
    "e", "ou",
    "ter", "têm", "tem", "até", "sobre", "como", "quais", "qual",
    "regras", "regra", "exigencias", "exigência", "exigências",
    # termos "meta" que frequentemente deixam o FTS restritivo demais
    "rdc", "lei", "decreto", "portaria", "resolucao", "resolução",
    "numero", "número", "ano",
    # opcional: costuma atrapalhar mais do que ajudar (muito frequente no texto)
    #"art",
}


def _build_fts_keywords_text(question: str, max_terms: int = 8) -> str:
    """
    Extrai uma versão "keywordizada" para FTS.
    Objetivo MVP: evitar que a pergunta inteira gere uma tsquery muito restritiva (AND demais).

    Heurísticas:
    - mantém tokens alfanuméricos (inclui números)
    - remove stopwords comuns
    - prioriza termos de domínio quando presentes (ex.: cannabis, thc)
    - limita quantidade para não inflar a query
    """
    q = (question or "").casefold()
    tokens = _TOKEN_RE.findall(q)
    if not tokens:
        return ""

    # Remove padrões comuns "tipo + número" (ex.: "rdc 327", "lei 9782", "decreto 1234")
    cleaned: List[str] = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t in {"rdc", "lei", "decreto", "portaria", "resolucao", "resolução"}:
            if i + 1 < len(tokens) and tokens[i + 1].isdigit():
                i += 2
                continue
        cleaned.append(t)
        i += 1
    tokens = cleaned


    # Filtra stopwords e tokens muito curtos (exceto números)
    filtered: List[str] = []
    for t in tokens:
        if t in _FTS_STOPWORDS:
            continue
        if t.isdigit():
            # Mantém só números pequenos (ex.: 0 e 2 do "0,2"); descarta 327/2019/9782 etc
            if len(t) <= 2:
                filtered.append(t)
            continue
        if len(t) >= 3:
            filtered.append(t)

    if not filtered:
        return ""

    # Prioriza domínio
    priority: List[str] = []
    for t in ("cannabis", "thc", "canabidiol"):
        if t in filtered and t not in priority:
            priority.append(t)

    # Completa com o restante, preservando ordem e limitando
    for t in filtered:
        if t not in priority:
            priority.append(t)
        if len(priority) >= max_terms:
            break

    return " ".join(priority).strip()


def _fts_hits(cur, pipeline_version: str, version_id: Optional[str], ts_func: str, text: str) -> int:
    """
    Conta quantos chunks batem com uma tsquery.
    ts_func: 'websearch' | 'plain' | 'or'
    """
    if not text.strip():
        return 0

    if ts_func == "websearch":
        q_sql = "websearch_to_tsquery('portuguese', %(q)s)"
    elif ts_func == "plain":
        q_sql = "plainto_tsquery('portuguese', %(q)s)"
    elif ts_func == "or":
        # OR entre termos: kw1 | kw2 | kw3 ...
        # Usa to_tsquery para aceitar operador |.
        # Atenção: tokens precisam ser "seguros" (só alfanuméricos) – já garantido por _TOKEN_RE.
        parts = [p for p in text.split() if p]
        or_q = " | ".join(parts)
        q_sql = "to_tsquery('portuguese', %(q)s)"
        text = or_q
    else:
        raise ValueError(f"ts_func inválida: {ts_func}")

    sql = f"""
    WITH q AS (SELECT {q_sql} AS q)
    SELECT COUNT(*)
    FROM embedding_chunks c
    JOIN document_versions v ON v.version_id = c.version_id
    CROSS JOIN q
    WHERE v.status = 'INDEXED'
      AND c.pipeline_version = %(pipeline_version)s
      AND ( %(version_id)s::uuid IS NULL OR c.version_id = %(version_id)s::uuid )
      AND c.tsv @@ q.q;
    """
    cur.execute(sql, {"q": text, "pipeline_version": pipeline_version, "version_id": version_id})
    return int(cur.fetchone()[0])


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

    # --- Seleção automática da estratégia FTS (resolve "pergunta natural -> 0 hits") ---
    # 1) websearch_to_tsquery(question)
    # 2) plainto_tsquery(keywords(question))
    # 3) to_tsquery OR entre keywords (mais permissivo)
    fts_mode = "websearch"
    fts_text = question

    sql = """
    WITH
    q AS (
      SELECT
        CASE
          WHEN %(fts_mode)s = 'websearch' THEN websearch_to_tsquery('portuguese', %(fts_text)s)
          WHEN %(fts_mode)s = 'plain' THEN plainto_tsquery('portuguese', %(fts_text)s)
          ELSE to_tsquery('portuguese', %(fts_text)s)
        END AS q
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

    with get_conn() as conn:
        with conn.cursor() as cur:
            # Decide modo do FTS apenas se FTS está habilitado (n1_fts > 0)
            if n1_fts > 0:
                hits = _fts_hits(cur, pipeline_version, version_id, "websearch", question)
                if hits == 0:
                    kw = _build_fts_keywords_text(question)
                    hits_kw = _fts_hits(cur, pipeline_version, version_id, "plain", kw)
                    if hits_kw > 0:
                        fts_mode = "plain"
                        fts_text = kw
                    else:
                        # Fallback final: OR entre keywords (to_tsquery com '|')
                        hits_or = _fts_hits(cur, pipeline_version, version_id, "or", kw)
                        if hits_or > 0:
                            fts_mode = "or"
                            # _fts_hits já transformou internamente, mas aqui precisamos do texto OR:
                            # Reaplica a transformação localmente
                            parts = [p for p in kw.split() if p]
                            fts_text = " | ".join(parts)
                        else:
                            # mantém websearch com question (vai retornar 0, mas é o comportamento mais "honesto")
                            fts_mode = "websearch"
                            fts_text = question

            params = {
                "pipeline_version": pipeline_version,
                "embedding_model_id": embedding_model_id,
                "version_id": version_id,
                "n1_fts": n1_fts,
                "n2_vec": n2_vec,
                "rrf_k": rrf_k,
                "top_k": top_k,
                "qvec": qvec,
                "fts_mode": fts_mode,
                "fts_text": fts_text,
            }
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
