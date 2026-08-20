from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import defaultdict
from typing import Any, Dict, List, Optional

from intelireg.db import get_conn
import intelireg.settings as settings
from intelireg.embeddings import embed_query_pgvector
from intelireg.regulatory_identifiers import (
    RegulatoryIdentifier,
    family_search_terms,
    identifier_debug_dict,
    number_title_variants,
    parse_regulatory_identifier,
    title_matches_identifier,
)

_TOKEN_RE = re.compile(r"[a-zA-ZÀ-ÿ0-9]+", re.UNICODE)

def normalize_for_hash(s: str) -> str:
    return " ".join((s or "").split()).casefold()


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


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

    Quando a pergunta contém um identificador regulatório explícito, preserva
    tipo/número/ano. A implementação anterior removia justamente pares como
    "RDC 476" e descartava números longos; isso tornava a busca por uma norma
    exata estruturalmente fraca.
    """
    q = (question or "").casefold()
    tokens = _TOKEN_RE.findall(q)
    if not tokens:
        return ""

    identifier = parse_regulatory_identifier(question)

    # Identificador normativo: preserva o sinal estruturado primeiro.
    if identifier is not None:
        family_term = {
            "rdc": "rdc",
            "in": "instrucao",
            "inc": "instrucao",
            "prt": "portaria",
            "res": "resolucao",
        }.get(identifier.family, identifier.family)

        priority: List[str] = [
            family_term,
            str(identifier.number),
        ]
        if identifier.year is not None:
            priority.append(str(identifier.year))

        for token in tokens:
            if token in _FTS_STOPWORDS:
                continue
            if token in {"n", "no", "nr", "numero"}:
                continue
            normalized_number = token.replace(".", "")
            if normalized_number in {str(identifier.number), str(identifier.year or "")}:
                continue
            if token.isdigit():
                continue
            if len(token) >= 3 and token not in priority:
                priority.append(token)
            if len(priority) >= max_terms:
                break

        return " ".join(priority[:max_terms]).strip()

    # Pergunta comum: mantém a heurística permissiva do MVP.
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

    filtered: List[str] = []
    for t in tokens:
        if t in _FTS_STOPWORDS:
            continue
        if t.isdigit():
            if len(t) <= 2:
                filtered.append(t)
            continue
        if len(t) >= 3:
            filtered.append(t)

    if not filtered:
        return ""

    priority: List[str] = []
    for t in ("cannabis", "thc", "canabidiol"):
        if t in filtered and t not in priority:
            priority.append(t)

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


_RERANK_STOPWORDS = {
    "quais", "qual", "que", "onde", "como", "sobre", "tratam", "trata",
    "disponiveis", "disponíveis", "normas", "atos", "regulatorios", "regulatórios",
    "o", "a", "os", "as", "um", "uma", "de", "do", "da", "dos", "das",
    "para", "por", "com", "sem", "em", "no", "na", "nos", "nas", "e", "ou",
}


def _fold_for_match(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", value.casefold()).strip()


def _light_stem(token: str) -> str:
    """
    Stem mínimo e determinístico só para cobertura lexical do reranker.
    Não substitui o stemmer do PostgreSQL; serve para aproximar singular/plural
    em casos como medicamento(s) e vacina(s).
    """
    token = _fold_for_match(token)
    if len(token) > 4 and token.endswith("s"):
        token = token[:-1]
    return token


def _meaningful_terms(question: str) -> List[str]:
    folded = _fold_for_match(question)
    tokens = _TOKEN_RE.findall(folded)
    terms: List[str] = []
    for token in tokens:
        token = _light_stem(token)
        if not token or token in _RERANK_STOPWORDS:
            continue
        if token.isdigit():
            continue
        if len(token) < 3:
            continue
        if token not in terms:
            terms.append(token)
    return terms


def lexical_coverage(question: str, title: str, text: str) -> float:
    terms = _meaningful_terms(question)
    if not terms:
        return 0.0

    candidate_tokens = {
        _light_stem(token)
        for token in _TOKEN_RE.findall(_fold_for_match(f"{title} {text}"))
        if token
    }
    matched = sum(1 for term in terms if term in candidate_tokens)
    return matched / len(terms)


def build_retrieval_plan(top_k: int, n1_fts: int, n2_vec: int) -> Dict[str, int]:
    """
    Calcula o pool interno usado antes do reranking.

    `n1_fts`/`n2_vec` continuam sendo parâmetros públicos solicitados pelo
    cliente. Quando o reranker está ativo, o servidor pode ampliar
    deterministicamente cada canal para evitar que um bom candidato seja
    descartado antes da fusão/reranking.
    """
    if not settings.RERANK_ENABLED:
        candidate_limit = max(1, top_k)
        return {
            "candidate_limit": candidate_limit,
            "effective_n1_fts": max(0, n1_fts),
            "effective_n2_vec": max(0, n2_vec),
        }

    expanded = max(top_k, top_k * settings.RERANK_CANDIDATE_MULTIPLIER)
    candidate_limit = max(
        1,
        top_k,
        min(expanded, settings.RERANK_CANDIDATES_MAX),
    )

    def effective(requested: int) -> int:
        if requested <= 0:
            return 0
        return min(
            max(requested, candidate_limit),
            settings.RETRIEVAL_CANDIDATES_MAX,
        )

    return {
        "candidate_limit": candidate_limit,
        "effective_n1_fts": effective(n1_fts),
        "effective_n2_vec": effective(n2_vec),
    }


def _find_exact_document_versions(
    cur,
    identifier: RegulatoryIdentifier,
    *,
    pipeline_version: str,
    version_id: Optional[str],
) -> List[Dict[str, Any]]:
    """
    Resolve um identificador contra títulos já existentes sem exigir reindexação.

    O filtro SQL só reduz o universo; a confirmação final é feita pelo parser
    Python para não confundir, por exemplo, RDC 17 com datas contendo "17".
    """
    number_patterns = [f"%{variant}%" for variant in number_title_variants(identifier.number)]
    if not number_patterns:
        return []

    clauses = ["d.title ILIKE %s" for _ in number_patterns]
    params: List[Any] = list(number_patterns)

    family_terms = family_search_terms(identifier)
    family_clauses = ["unaccent(d.title) ILIKE unaccent(%s)" for _ in family_terms]
    family_patterns = [f"%{term}%" for term in family_terms]
    if identifier.doc_types:
        family_filter = (
            " AND (d.doc_type = ANY(%s) OR "
            + " OR ".join(family_clauses)
            + ")"
        )
        params.append(list(identifier.doc_types))
        params.extend(family_patterns)
    else:
        family_filter = " AND (" + " OR ".join(family_clauses) + ")"
        params.extend(family_patterns)

    date_clause = ""
    if identifier.publication_date is not None:
        date_clause = " AND d.title ILIKE %s"
        params.append(f"%{identifier.publication_date.strftime('%d/%m/%Y')}%")
    elif identifier.year is not None:
        date_clause = " AND d.title ILIKE %s"
        params.append(f"%{identifier.year}%")

    version_clause = ""
    if version_id is not None:
        version_clause = " AND v.version_id = %s::uuid"
        params.append(version_id)

    params.extend([pipeline_version, settings.REGULATORY_IDENTIFIER_LOOKUP_LIMIT])

    sql = f"""
        SELECT DISTINCT
            d.document_id,
            d.title,
            d.doc_type,
            v.version_id,
            v.captured_at
        FROM documents d
        JOIN document_versions v ON v.document_id = d.document_id
        WHERE v.status = 'INDEXED'
          AND ({' OR '.join(clauses)})
          {family_filter}
          {date_clause}
          {version_clause}
          AND EXISTS (
              SELECT 1
              FROM embedding_chunks c
              WHERE c.version_id = v.version_id
                AND c.pipeline_version = %s
          )
        ORDER BY v.captured_at DESC NULLS LAST, d.title ASC
        LIMIT %s
    """
    cur.execute(sql, params)

    matched: List[Dict[str, Any]] = []
    seen_documents: set[str] = set()
    for document_id, title, doc_type, candidate_version_id, captured_at in cur.fetchall():
        if not title_matches_identifier(title, identifier):
            continue
        document_key = str(document_id)
        if document_key in seen_documents:
            continue
        seen_documents.add(document_key)
        matched.append(
            {
                "document_id": document_key,
                "title": title,
                "doc_type": doc_type,
                "version_id": str(candidate_version_id),
                "captured_at": captured_at,
            }
        )
    return matched


def _fetch_exact_identifier_chunks(
    cur,
    exact_documents: List[Dict[str, Any]],
    *,
    pipeline_version: str,
    max_chunks: int,
) -> List[Dict[str, Any]]:
    if not exact_documents or max_chunks <= 0:
        return []

    results: List[Dict[str, Any]] = []
    for exact_rank, document in enumerate(exact_documents, start=1):
        if len(results) >= max_chunks:
            break
        remaining = max_chunks - len(results)
        cur.execute(
            """
            SELECT
              c.chunk_id,
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
            FROM embedding_chunks c
            JOIN document_versions v ON v.version_id = c.version_id
            JOIN documents d ON d.document_id = v.document_id
            WHERE c.version_id = %s::uuid
              AND c.pipeline_version = %s
            ORDER BY c.chunk_index ASC
            LIMIT %s
            """,
            (document["version_id"], pipeline_version, remaining),
        )
        for row in cur.fetchall():
            results.append(
                {
                    "chunk_id": str(row[0]),
                    "rrf_score": 0.0,
                    "fts_rank": None,
                    "fts_score": None,
                    "vec_rank": None,
                    "vec_distance": None,
                    "version_id": str(row[1]),
                    "pipeline_version": row[2],
                    "chunk_index": row[3],
                    "tokens_count": row[4],
                    "text": row[5],
                    "node_refs": row[6],
                    "document": {
                        "document_id": str(row[7]),
                        "title": row[8],
                        "source_org": row[9],
                        "doc_type": row[10],
                        "source_url": row[11],
                        "final_url": row[12],
                        "captured_at": row[13].isoformat() if row[13] is not None else None,
                    },
                    "exact_identifier_match": True,
                    "exact_identifier_rank": exact_rank,
                }
            )
    return results


def _merge_candidates(
    base_candidates: List[Dict[str, Any]],
    exact_candidates: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    by_chunk: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []

    for candidate in base_candidates + exact_candidates:
        chunk_id = candidate["chunk_id"]
        existing = by_chunk.get(chunk_id)
        if existing is None:
            by_chunk[chunk_id] = dict(candidate)
            order.append(chunk_id)
            continue

        if candidate.get("exact_identifier_match"):
            existing["exact_identifier_match"] = True
            existing["exact_identifier_rank"] = candidate.get("exact_identifier_rank")

    return [by_chunk[chunk_id] for chunk_id in order]


def rerank_candidates(
    question: str,
    candidates: List[Dict[str, Any]],
    *,
    top_k: int,
) -> List[Dict[str, Any]]:
    """
    Reranking determinístico e auditável.

    - preserva o RRF como sinal base;
    - promove correspondência exata de identificador sem fingir que ela é RRF;
    - usa cobertura lexical leve para desempatar casos semanticamente próximos;
    - diversificação é opcional e desligada por padrão.
    """
    if top_k <= 0:
        return []

    scored: List[Dict[str, Any]] = []
    for candidate in candidates:
        item = dict(candidate)
        coverage = lexical_coverage(
            question,
            item.get("document", {}).get("title", ""),
            item.get("text", ""),
        )
        exact_match = bool(item.get("exact_identifier_match", False))
        base_rrf = float(item.get("rrf_score") or 0.0)

        final_score = base_rrf
        if settings.RERANK_ENABLED:
            # Cobertura alta recebe sinal forte; correspondências parciais não
            # devem dominar o RRF apenas por compartilhar termos genéricos.
            final_score += settings.RERANK_LEXICAL_WEIGHT * (coverage ** 2)
        if exact_match:
            final_score += settings.RERANK_EXACT_IDENTIFIER_WEIGHT

        item["lexical_coverage"] = coverage
        item["final_score"] = final_score
        item.setdefault("exact_identifier_match", False)
        item.setdefault("exact_identifier_rank", None)
        scored.append(item)

    scored.sort(
        key=lambda item: (
            1 if item.get("exact_identifier_match") else 0,
            float(item.get("final_score") or 0.0),
            float(item.get("rrf_score") or 0.0),
            -(item.get("fts_rank") or 10**9),
            -(item.get("vec_rank") or 10**9),
        ),
        reverse=True,
    )

    if not settings.RERANK_DIVERSITY_ENABLED:
        return scored[:top_k]

    selected: List[Dict[str, Any]] = []
    deferred: List[Dict[str, Any]] = []
    per_document: Dict[str, int] = defaultdict(int)
    cap = settings.RERANK_MAX_CHUNKS_PER_DOCUMENT

    for item in scored:
        document_id = item.get("document", {}).get("document_id", "")
        if document_id and per_document[document_id] >= cap:
            deferred.append(item)
            continue
        selected.append(item)
        if document_id:
            per_document[document_id] += 1
        if len(selected) >= top_k:
            return selected

    # Soft cap: se faltarem resultados, devolve os melhores adiados em vez de
    # inserir candidatos mais fracos apenas para "forçar diversidade".
    for item in deferred:
        selected.append(item)
        if len(selected) >= top_k:
            break
    return selected


def retrieval_debug_summary(
    question: str,
    rows: List[Dict[str, Any]],
    *,
    top_k: int,
    n1_fts: int,
    n2_vec: int,
) -> Dict[str, Any]:
    identifier = parse_regulatory_identifier(question)
    plan = build_retrieval_plan(top_k, n1_fts, n2_vec)
    return {
        "strategy_version": "hybrid-rerank-v2",
        "candidate_limit": plan["candidate_limit"],
        "effective_n1_fts": plan["effective_n1_fts"],
        "effective_n2_vec": plan["effective_n2_vec"],
        "identifier": identifier_debug_dict(identifier),
        "identifier_lookup_enabled": bool(
            settings.REGULATORY_IDENTIFIER_LOOKUP_ENABLED
        ),
        "rerank_enabled": bool(settings.RERANK_ENABLED),
        "diversity_enabled": bool(settings.RERANK_DIVERSITY_ENABLED),
        "result_count": len(rows),
    }

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
    if n1_fts <= 0 and n2_vec <= 0:
        return []

    qvec: Optional[str] = None
    if n2_vec > 0:
        qvec = embed_query_pgvector(question, embedding_model_id=embedding_model_id)

    identifier = (
        parse_regulatory_identifier(question)
        if settings.REGULATORY_IDENTIFIER_LOOKUP_ENABLED
        else None
    )
    plan = build_retrieval_plan(top_k, n1_fts, n2_vec)
    candidate_limit = plan["candidate_limit"]
    effective_n1_fts = plan["effective_n1_fts"]
    effective_n2_vec = plan["effective_n2_vec"]

    # --- Seleção automática da estratégia FTS (resolve "pergunta natural -> 0 hits") ---
    # 1) websearch_to_tsquery(question)
    # 2) plainto_tsquery(keywords(question))
    # 3) to_tsquery OR entre keywords (mais permissivo)
    fts_mode = "websearch"
    fts_text = question

    sql_hybrid = """
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
      LIMIT %(effective_n1_fts)s
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
      LIMIT %(effective_n2_vec)s
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
    LIMIT %(candidate_limit)s;
    """
    # FTS-only (n2_vec = 0)
    sql_fts_only = """
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
      LIMIT %(effective_n1_fts)s
    ),
    scored AS (
      SELECT
        f.chunk_id,
        f.r_fts,
        f.s_fts,
        NULL::int AS r_vec,
        NULL::double precision AS d_vec,
        (1.0 / (%(rrf_k)s + f.r_fts)) AS rrf_score
      FROM fts f
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
    LIMIT %(candidate_limit)s;
    """

    # Vec-only (n1_fts = 0)
    sql_vec_only = """
    WITH
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
      LIMIT %(effective_n2_vec)s
    ),
    scored AS (
      SELECT
        v.chunk_id,
        NULL::int AS r_fts,
        NULL::double precision AS s_fts,
        v.r_vec,
        v.d_vec,
        (1.0 / (%(rrf_k)s + v.r_vec)) AS rrf_score
      FROM vec v
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
    LIMIT %(candidate_limit)s;
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
                "effective_n1_fts": effective_n1_fts,
                "effective_n2_vec": effective_n2_vec,
                "rrf_k": rrf_k,
                "top_k": top_k,
                "candidate_limit": candidate_limit,
                "qvec": qvec,
                "fts_mode": fts_mode,
                "fts_text": fts_text,
            }
            
            # pgvector (HNSW): aumenta recall/estabilidade do ranking (custo: CPU)
            if n2_vec and n2_vec > 0:
                cur.execute(f"SET hnsw.ef_search = {settings.HNSW_EF_SEARCH}")


            if n1_fts > 0 and n2_vec > 0:
                cur.execute(sql_hybrid, params)
            elif n1_fts > 0 and n2_vec <= 0:
                # FTS-only: não exige qvec, mas params pode conter sem problema
                cur.execute(sql_fts_only, params)
            else:
                # vec-only
                if qvec is None:
                    raise RuntimeError("n2_vec > 0 mas qvec não foi calculado (embedding_model_id inválido?)")
                cur.execute(sql_vec_only, params)
            rows = cur.fetchall()

            exact_candidates: List[Dict[str, Any]] = []
            if identifier is not None:
                exact_documents = _find_exact_document_versions(
                    cur,
                    identifier,
                    pipeline_version=pipeline_version,
                    version_id=version_id,
                )
                exact_candidates = _fetch_exact_identifier_chunks(
                    cur,
                    exact_documents,
                    pipeline_version=pipeline_version,
                    max_chunks=settings.REGULATORY_IDENTIFIER_MAX_CHUNKS,
                )

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
                "exact_identifier_match": False,
                "exact_identifier_rank": None,
            }
        )

    merged = _merge_candidates(results, exact_candidates)
    return rerank_candidates(question, merged, top_k=top_k)
