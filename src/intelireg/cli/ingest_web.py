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
_art_re = re.compile(r"^Art\.\s*(\d+)\s*([º°]|\b)?\.?\s*(.*)$", re.IGNORECASE)
_cap_re = re.compile(r"^CAP[ÍI]TULO\s+([IVXLC\d]+)\b", re.IGNORECASE)
_sec_re = re.compile(r"^Se[cç]ão\s+([IVXLC\d]+)\b", re.IGNORECASE)
_subsec_re = re.compile(r"^Subse[cç]ão\s+([IVXLC\d]+)\b", re.IGNORECASE)
_anexo_re = re.compile(r"^ANEXO(\s+[IVXLC\d]+)?\b", re.IGNORECASE)

_SKIP_LINE_RE = re.compile(r"^Este texto n[aã]o substitui a Publica[cç][aã]o Oficial\.?$", re.IGNORECASE)


def normalize_text(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    s = s.replace("\u00a0", " ")  # nbsp
    s = _ws_re.sub(" ", s).strip()
    return s


def normalize_text_keep_newlines(s: str) -> str:
    """
    Normaliza preservando quebras de linha entre parágrafos/blocos.
    (Bom para normas: melhora leitura, FTS e chunking.)
    """
    s = unicodedata.normalize("NFKC", s or "")
    s = s.replace("\u00a0", " ")
    lines = []
    for raw in s.splitlines():
        line = _ws_re.sub(" ", raw).strip()
        if line:
            lines.append(line)
    return "\n".join(lines).strip()


def normalize_for_hash(s: str) -> str:
    # determinístico para deduplicação (colapsa whitespace e casefold)
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


# --------- Extração (modelo comum e Datalegis) ---------

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
    # remove blocos comuns de navegação (para páginas normais)
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
                break
            if isinstance(el, Tag) and el.name in ("p", "li"):
                t = normalize_text(el.get_text(" ", strip=True))
                if t:
                    texts.append(t)

        section_text = normalize_text(" \n".join(texts))
        if section_text:
            raw_sections.append((level, heading_text, section_text))

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

    stack: List[tuple[int, str]] = []
    nodes: List[NodeDraft] = []
    all_text_for_hash: List[str] = []

    for level, heading_text, section_text in raw_sections:
        seg = f"h{level}-{slugify(heading_text)}"
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


def _looks_like_center_heading(p: Tag, text: str) -> bool:
    style = (p.get("style") or "").lower()
    centered = "text-align" in style and "center" in style
    if not centered:
        return False
    t = text.strip()
    if len(t) < 3 or len(t) > 140:
        return False
    # evita classificar coisas enormes como heading
    if len(t.split()) > 14:
        return False
    # se contém "Art." não é heading genérico
    if _art_re.match(t):
        return False
    return True


def _table_to_text(table: Tag) -> str:
    rows_txt: List[str] = []
    for tr in table.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        if not cells:
            continue
        parts = [normalize_text(c.get_text(" ", strip=True)) for c in cells]
        line = " | ".join([p for p in parts if p])
        if line:
            rows_txt.append(line)
    return "\n".join(rows_txt).strip()


def extract_nodes_datalegis(html: str) -> Optional[tuple[str, List[NodeDraft], str]]:
    """
    Extractor específico para páginas Datalegis/AnvisaLegis:
    - conteúdo principal em div.ato
    - estrutura via parágrafos (CAPÍTULO, Seção, Art.)
    - tabelas em anexos
    """
    soup = BeautifulSoup(html, "html.parser")
    # não decompõe nav/header/footer aqui, porque vamos isolar por div.ato
    for t in soup(["script", "style", "noscript"]):
        t.decompose()

    ato = soup.select_one("div.ato")
    if not ato:
        return None

    title = extract_title(soup)

    # blocos em ordem: p fora de table + tables
    blocks: List[Tag] = []
    for el in ato.find_all(["p", "table"], recursive=True):
        if el.name == "p" and el.find_parent("table"):
            continue
        blocks.append(el)

    # stack hierárquico (níveis semânticos)
    # níveis: 1=CAP/ANEXO, 2=título central (DISPOSIÇÕES...), 3=Seção/Subseção, 4=subtítulo central (Objetivos...), 5=Artigo
    stack: List[tuple[int, str, str]] = []  # (level, segment, label)
    nodes: List[NodeDraft] = []

    def current_parent_path() -> Optional[str]:
        if not stack:
            return None
        return "/".join(seg for _, seg, _ in stack)

    def push_heading(level: int, label: str) -> NodeDraft:
        nonlocal stack
        seg = slugify(label, max_len=80)
        while stack and stack[-1][0] >= level:
            stack.pop()
        parent_path = "/".join(seg for _, seg, _ in stack) if stack else None
        stack.append((level, seg, label))
        path = "/".join(seg for _, seg, _ in stack)

        return NodeDraft(
            heading_level=level,
            heading_text=label,
            path=path,
            parent_path=parent_path,
            text_normalized="",  # heading puro (vai entrar no chunk via heading_text)
        )

    # preâmbulo (antes do 1º Art./CAP/ANEXO)
    preamble_parts: List[str] = []
    in_preamble = True

    # artigo em construção
    art_heading: Optional[str] = None
    art_level = 5
    art_path: Optional[str] = None
    art_parent: Optional[str] = None
    art_parts: List[str] = []

    # anexo em construção (1 node por anexo)
    annex_heading: Optional[str] = None
    annex_path: Optional[str] = None
    annex_parent: Optional[str] = None
    annex_parts: List[str] = []

    def flush_preamble() -> None:
        nonlocal preamble_parts, in_preamble
        txt = normalize_text_keep_newlines("\n".join(preamble_parts))
        if txt:
            nodes.append(
                NodeDraft(
                    heading_level=1,
                    heading_text="Preâmbulo",
                    path="preambulo",
                    parent_path=None,
                    text_normalized=txt,
                )
            )
        preamble_parts = []
        in_preamble = False

    def flush_article() -> None:
        nonlocal art_heading, art_path, art_parent, art_parts
        if not art_heading or not art_path:
            art_heading = None
            art_path = None
            art_parent = None
            art_parts = []
            return
        txt = normalize_text_keep_newlines("\n".join(art_parts))
        nodes.append(
            NodeDraft(
                heading_level=art_level,
                heading_text=art_heading,
                path=art_path,
                parent_path=art_parent,
                text_normalized=txt,
            )
        )
        art_heading = None
        art_path = None
        art_parent = None
        art_parts = []

    def flush_annex() -> None:
        nonlocal annex_heading, annex_path, annex_parent, annex_parts
        if not annex_heading or not annex_path:
            annex_heading = None
            annex_path = None
            annex_parent = None
            annex_parts = []
            return
        txt = normalize_text_keep_newlines("\n".join(annex_parts))
        nodes.append(
            NodeDraft(
                heading_level=1,
                heading_text=annex_heading,
                path=annex_path,
                parent_path=annex_parent,
                text_normalized=txt,
            )
        )
        annex_heading = None
        annex_path = None
        annex_parent = None
        annex_parts = []

    def start_article(art_label: str, art_num: str, remainder: str) -> None:
        nonlocal art_heading, art_path, art_parent, art_parts
        flush_article()
        # Artigo sempre “abaixo” da pilha atual
        parent = current_parent_path()
        seg = f"art-{art_num}"
        art_parent = parent
        art_path = f"{parent}/{seg}" if parent else seg
        art_heading = art_label
        art_parts = []
        if remainder:
            art_parts.append(remainder.strip())

    def start_annex(label: str) -> None:
        nonlocal annex_heading, annex_path, annex_parent, annex_parts
        flush_article()
        flush_annex()
        # anexo vira raiz semântica: limpa stack e cria heading
        stack.clear()
        hnode = push_heading(1, label)
        nodes.append(hnode)
        annex_heading = label
        annex_parent = hnode.parent_path
        annex_path = hnode.path + "/conteudo"
        annex_parts = []

    for el in blocks:
        if el.name == "p":
            t = normalize_text(el.get_text(" ", strip=True))
        else:
            t = _table_to_text(el)

        if not t:
            continue
        if _SKIP_LINE_RE.match(t):
            continue

        # Detectores semânticos
        is_art = _art_re.match(t)
        is_cap = _cap_re.match(t)
        is_sec = _sec_re.match(t)
        is_subsec = _subsec_re.match(t)
        is_anexo = _anexo_re.match(t)

        # Primeira âncora semântica encerra o preâmbulo
        if in_preamble and (is_art or is_cap or is_anexo):
            flush_preamble()

        if in_preamble:
            # ainda no preâmbulo: guarda tudo
            preamble_parts.append(t)
            continue

        # Se estamos dentro de anexo, quase tudo vira conteúdo do anexo
        # (exceto início de outro ANEXO)
        if annex_heading and not is_anexo:
            annex_parts.append(t)
            continue

        if is_anexo:
            start_annex(t)
            continue

        if is_cap:
            # mudança de capítulo: fecha artigo e reseta hierarquia abaixo
            flush_article()
            # capítulo é nível 1
            hnode = push_heading(1, t)
            nodes.append(hnode)
            continue

        if is_sec:
            flush_article()
            hnode = push_heading(3, t)
            nodes.append(hnode)
            continue

        if is_subsec:
            flush_article()
            hnode = push_heading(3, t)
            nodes.append(hnode)
            continue

        if is_art:
            m = _art_re.match(t)
            assert m
            art_num = m.group(1)
            # label do heading: "Art. N°" (com símbolo se existir)
            # reconstrução conservadora:
            head = f"Art. {art_num}"
            if "º" in t or "°" in t:
                # tenta preservar símbolo
                head = re.match(r"^(Art\.\s*\d+\s*[º°]?)", t, re.IGNORECASE).group(1)  # type: ignore[union-attr]
            remainder = (m.group(3) or "").strip()
            start_article(head, art_num, remainder)
            continue

        # headings centrados genéricos (DISPOSIÇÕES..., Objetivos, etc.)
        if isinstance(el, Tag) and el.name == "p" and _looks_like_center_heading(el, t):
            flush_article()
            # heurística: se já temos capítulo/estrutura, isso vira nível 2 ou 4
            last_level = stack[-1][0] if stack else 0
            lvl = 2 if last_level <= 1 else 4
            hnode = push_heading(lvl, t)
            nodes.append(hnode)
            continue

        # Conteúdo normal
        if art_heading:
            art_parts.append(t)
        else:
            # texto solto fora de artigo/anexo: gruda como “pós-heading” no preâmbulo tardio
            # (raro, mas evita perda)
            preamble_parts.append(t)

    # flush finais
    if in_preamble:
        flush_preamble()
    flush_article()
    flush_annex()

    # hash global do conteúdo (inclui heading_text + texto)
    all_for_hash: List[str] = []
    for n in nodes:
        all_for_hash.append(n.heading_text)
        if n.text_normalized:
            all_for_hash.append(n.text_normalized)
    content_hash = sha256_hex(normalize_for_hash("\n".join(all_for_hash)))

    return title, nodes, content_hash


def extract_nodes_auto(html: str, max_heading_level: int) -> tuple[str, List[NodeDraft], str]:
    """
    Tenta Datalegis primeiro (normas), senão usa headings (web comum).
    """
    maybe = extract_nodes_datalegis(html)
    if maybe:
        return maybe
    return extract_nodes_by_headings(html, max_heading_level=max_heading_level)


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
        description="Ingestão MVP: URL -> nodes -> READY_FOR_INDEX + enqueue IndexVersionJob"
    )
    ap.add_argument("--url", required=True)
    ap.add_argument("--source-org", required=True)
    ap.add_argument("--doc-type", required=True)

    ap.add_argument(
        "--max-heading-level",
        type=int,
        default=settings.CANON_MAX_HEADING_LEVEL,
        help="Nível máximo de heading a considerar (H1..Hn) quando NÃO for Datalegis.",
    )

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

    # 1) fetch HTML
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

    # 2) canonicalizar em nodes (Datalegis ou headings) e gerar content_hash
    title, node_drafts, content_hash = extract_nodes_auto(
        html, max_heading_level=args.max_heading_level
    )

    # 3) preparar inserts no banco
    version_id = str(uuid4())

    node_id_by_path: dict[str, str] = {}
    node_rows = []

    for i, nd in enumerate(node_drafts):
        node_id = str(uuid4())
        node_id_by_path[nd.path] = node_id
        parent_id = node_id_by_path.get(nd.parent_path) if nd.parent_path else None
        node_rows.append(
            (
                node_id,
                version_id,
                i,  # node_index determinístico
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
            existing_version = find_version_id_by_content_hash(cur, content_hash)
            if existing_version:
                print(
                    f"[ingest_web] conteúdo já existe (content_hash). version_id existente: {existing_version}"
                )
                return

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

    # 4) enfileirar indexação
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
