from __future__ import annotations

import argparse
import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional
from uuid import uuid4

import httpx
from bs4 import BeautifulSoup
from bs4.element import Tag

from intelireg import settings
from intelireg.db import get_conn
from intelireg.jobs import enqueue_job


# --------- Normalização e utilitários ---------

_ws_re = re.compile(r"\s+")


def normalize_text(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    s = s.replace("\u00a0", " ")  # nbsp
    s = _ws_re.sub(" ", s).strip()
    return s


def normalize_for_hash(s: str) -> str:
    # determinístico para deduplicação
    return normalize_text(s).casefold()


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def slugify(s: str, max_len: int = 80) -> str:
    s = normalize_text(s)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    if not s:
        s = "secao"
    return s[:max_len].strip("-") or "secao"


# --------- Extração por headings ---------

@dataclass
class NodeDraft:
    heading_level: int
    heading_text: str
    path: str
    parent_path: Optional[str]
    text_normalized: str


def extract_title(soup: BeautifulSoup) -> str:
    if soup.title and soup.title.get_text(strip=True):
        return normalize_text(soup.title.get_text(" ", strip=True))
    h1 = soup.find("h1")
    if h1:
        return normalize_text(h1.get_text(" ", strip=True))
    return "Documento"


def prune_noise(soup: BeautifulSoup) -> None:
    # remove tags que só atrapalham o MVP
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    # remove blocos comuns de navegação
    for t in soup.find_all(["nav", "header", "footer", "aside"]):
        t.decompose()


def extract_nodes_by_headings(
    html: str, max_heading_level: int = 3
) -> tuple[str, List[NodeDraft], str]:
    soup = BeautifulSoup(html, "html.parser")
    prune_noise(soup)

    body = soup.body or soup
    title = extract_title(soup)

    heading_names = [f"h{i}" for i in range(1, max_heading_level + 1)]
    headings = body.find_all(heading_names)

    # fallback se não houver headings
    if not headings:
        full_text = normalize_text(body.get_text(" ", strip=True))
        content_hash = sha256_hex(normalize_for_hash(full_text))
        node = NodeDraft(
            heading_level=1,
            heading_text=title or "Documento",
            path="h1-" + slugify(title or "documento"),
            parent_path=None,
            text_normalized=full_text,
        )
        return title, [node], content_hash

    # varrer seções
    raw_sections: List[tuple[int, str, str]] = []
    for h in headings:
        level = int(h.name[1])
        heading_text = normalize_text(h.get_text(" ", strip=True))
        texts: List[str] = []

        for el in h.next_elements:
            if isinstance(el, Tag) and el.name in heading_names:
                # chegou no próximo heading (da faixa 1..max_heading_level)
                break
            if isinstance(el, Tag) and el.name in ("p", "li"):
                t = normalize_text(el.get_text(" ", strip=True))
                if t:
                    texts.append(t)

        section_text = normalize_text(" \n".join(texts))
        if section_text:
            raw_sections.append((level, heading_text, section_text))

    # fallback se headings existirem mas não coletarmos texto
    if not raw_sections:
        full_text = normalize_text(body.get_text(" ", strip=True))
        content_hash = sha256_hex(normalize_for_hash(full_text))
        node = NodeDraft(
            heading_level=1,
            heading_text=title or "Documento",
            path="h1-" + slugify(title or "documento"),
            parent_path=None,
            text_normalized=full_text,
        )
        return title, [node], content_hash

    # construir paths e hierarquia por nível
    stack: List[tuple[int, str]] = []  # (level, path_segment)
    nodes: List[NodeDraft] = []
    all_text_for_hash: List[str] = []

    for level, heading_text, section_text in raw_sections:
        seg = f"h{level}-{slugify(heading_text)}"

        # ajusta stack (hierarquia por nível)
        while stack and stack[-1][0] >= level:
            stack.pop()

        parent_path = "/".join([s for _, s in stack]) if stack else None
        stack.append((level, seg))
        path = "/".join([s for _, s in stack])

        nodes.append(
            NodeDraft(
                heading_level=level,
                heading_text=heading_text,
                path=path,
                parent_path=parent_path,
                text_normalized=section_text,
            )
        )
        all_text_for_hash.append(section_text)

    content_hash = sha256_hex(normalize_for_hash("\n".join(all_text_for_hash)))
    return title, nodes, content_hash


# --------- DB helpers: upsert simplificado por URL / dedup por content_hash ---------

def find_document_id_by_url(cur, url: str) -> Optional[str]:
    cur.execute(
        """
        SELECT d.document_id
        FROM documents d
        JOIN document_versions v ON v.document_id = d.document_id
        WHERE v.source_url = %s OR v.final_url = %s
        ORDER BY v.created_at DESC
        LIMIT 1
        """,
        (url, url),
    )
    row = cur.fetchone()
    return row[0] if row else None


def find_version_id_by_content_hash(cur, content_hash: str) -> Optional[str]:
    cur.execute(
        "SELECT version_id FROM document_versions WHERE content_hash = %s LIMIT 1",
        (content_hash,),
    )
    row = cur.fetchone()
    return row[0] if row else None


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Ingestão MVP: URL -> nodes (headings) -> READY_FOR_INDEX + enqueue IndexVersionJob"
    )
    ap.add_argument("--url", required=True)
    ap.add_argument("--source-org", required=True)
    ap.add_argument("--doc-type", required=True)

    ap.add_argument(
        "--max-heading-level",
        type=int,
        default=settings.CANON_MAX_HEADING_LEVEL,
        help="Nível máximo de heading a considerar (H1..Hn).",
    )

    # defaults centralizados em settings.py (mas ainda passáveis por CLI)
    ap.add_argument(
        "--pipeline-version",
        default=settings.PIPELINE_VERSION,
        help="Versão do pipeline (mudou chunking/embeddings => bump).",
    )
    ap.add_argument(
        "--embedding-model-id",
        default=settings.EMBEDDING_MODEL_ID,
        help="Identificador do modelo de embeddings (rastreabilidade).",
    )

    args = ap.parse_args()

    pipeline_version = args.pipeline_version
    embedding_model_id = args.embedding_model_id

    # 1) fetch HTML (apenas na ingestão)
    with httpx.Client(
        follow_redirects=True,
        timeout=30.0,
        headers={"User-Agent": "InteliReg-MVP/0.1"},
    ) as client:
        resp = client.get(args.url)

    final_url = str(resp.url)
    http_status = int(resp.status_code)
    captured_at = datetime.now(timezone.utc)

    if http_status < 200 or http_status >= 300:
        raise SystemExit(
            f"HTTP {http_status} ao buscar {args.url} (final_url={final_url})"
        )

    html = resp.text

    # 2) canonicalizar em nodes por headings e gerar content_hash
    title, node_drafts, content_hash = extract_nodes_by_headings(
        html, max_heading_level=args.max_heading_level
    )

    # 3) preparar inserts no banco (snapshot derivado = nodes)
    version_id = str(uuid4())

    # mapeamento path -> node_id para parent_id
    node_id_by_path: dict[str, str] = {}
    node_rows = []

    # IMPORTANT: node_index determinístico (ordem de extração)
    for i, nd in enumerate(node_drafts):
        node_id = str(uuid4())
        node_id_by_path[nd.path] = node_id
        parent_id = node_id_by_path.get(nd.parent_path) if nd.parent_path else None
        node_rows.append(
            (
                node_id,
                version_id,
                i,  # node_index
                nd.path,
                parent_id,
                nd.heading_text,
                nd.heading_level,
                nd.text_normalized,
            )
        )

    document_id: Optional[str] = None

    with get_conn() as conn:
        with conn.cursor() as cur:
            # dedup global por content_hash (decisão do MVP)
            existing_version = find_version_id_by_content_hash(cur, content_hash)
            if existing_version:
                print(
                    f"[ingest_web] conteúdo já existe (content_hash). version_id existente: {existing_version}"
                )
                return

            # tenta reaproveitar documento se já existe versão com a mesma URL/final_url
            document_id = find_document_id_by_url(cur, args.url)

            if not document_id:
                document_id = str(uuid4())
                cur.execute(
                    """
                    INSERT INTO documents (document_id, title, source_org, doc_type)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (document_id, title or "Documento", args.source_org, args.doc_type),
                )

            cur.execute(
                """
                INSERT INTO document_versions
                  (version_id, document_id, status, source_url, final_url, http_status, captured_at, content_hash)
                VALUES
                  (%s, %s, 'READY_FOR_INDEX', %s, %s, %s, %s, %s)
                """,
                (
                    version_id,
                    document_id,
                    args.url,
                    final_url,
                    http_status,
                    captured_at,
                    content_hash,
                ),
            )

            cur.executemany(
                """
                INSERT INTO nodes
                  (node_id, version_id, node_index, kind, path, parent_id, heading_text, heading_level, text_normalized)
                VALUES
                  (%s, %s, %s, 'heading_section', %s, %s, %s, %s, %s)
                """,
                node_rows,
            )

        conn.commit()

    # 4) enfileirar indexação (sem refetch)
    job_id = enqueue_job(
        "IndexVersionJob",
        {
            "version_id": version_id,
            "pipeline_version": pipeline_version,
            "embedding_model_id": embedding_model_id,
        },
    )

    print(
        f"[ingest_web] ok version_id={version_id} document_id={document_id} nodes={len(node_rows)} content_hash={content_hash}"
    )
    print(f"[ingest_web] enqueued IndexVersionJob job_id={job_id}")


if __name__ == "__main__":
    main()
