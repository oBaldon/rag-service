from __future__ import annotations

import argparse
import collections
import itertools
import math
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

# Art. 10º ... / Art. 10° ... / Art. 10. ...
_art_re = re.compile(r"^Art\.\s*(\d+)\s*([º°])?\s*\.?\s*(.*)$", re.IGNORECASE)

_cap_re = re.compile(r"^CAP[ÍI]TULO\s+([IVXLC\d]+)\b", re.IGNORECASE)
_sec_re = re.compile(r"^Se[cç]ão\s+([IVXLC\d]+)\b", re.IGNORECASE)
_subsec_re = re.compile(r"^Subse[cç]ão\s+([IVXLC\d]+)\b", re.IGNORECASE)
_anexo_re = re.compile(r"^ANEXO(\s+[IVXLC\d]+)?\b", re.IGNORECASE)

_SKIP_LINE_RE = re.compile(
    r"^Este texto n[aã]o substitui a Publica[cç][aã]o Oficial\.?$", re.IGNORECASE
)

_art_split_re = re.compile(r"(?=Art\.\s*\d+)", re.IGNORECASE)


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
    lines: List[str] = []
    for raw in s.splitlines():
        line = _ws_re.sub(" ", raw).strip()
        if line:
            lines.append(line)
    return "\n".join(lines).strip()


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


# --------- Fallback: páginas comuns com headings ---------

def extract_nodes_by_headings(
    html: str, max_heading_level: int = 3
) -> tuple[str, List[NodeDraft], str]:
    soup = BeautifulSoup(html, "html.parser")
    prune_noise(soup)

    body = soup.body or soup
    title = extract_title(soup)

    heading_names = [f"h{i}" for i in range(1, max_heading_level + 1)]
    headings = body.find_all(heading_names)

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

        section_text = normalize_text("\n".join(texts))
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
        all_text_for_hash.append(heading_text)
        all_text_for_hash.append(section_text)

    content_hash = sha256_hex(normalize_for_hash("\n".join(all_text_for_hash)))
    return title, nodes, content_hash


# --------- Datalegis / AnvisaLegis ---------

def _looks_like_center_heading(p: Tag, text: str) -> bool:
    style = (p.get("style") or "").lower()
    centered = ("text-align" in style) and ("center" in style)
    if not centered:
        return False

    t = text.strip()
    if len(t) < 3 or len(t) > 140:
        return False
    if len(t.split()) > 14:
        return False
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


def extract_nodes_datalegis(
    html: str,
) -> Optional[tuple[str, List[NodeDraft], str]]:
    """
    Extractor específico para Datalegis/AnvisaLegis:
    - conteúdo principal em div.ato
    - estrutura via parágrafos (CAPÍTULO, Seção/Subseção, Art.)
    - tabelas em anexos (e também podem aparecer no corpo)
    """
    soup = BeautifulSoup(html, "html.parser")
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

    # níveis semânticos:
    # 1=CAP/ANEXO, 2=título centrado (DISPOSIÇÕES...), 3=Seção, 4=Subseção/itens centrados, 5=Artigo
    stack: List[tuple[int, str, str]] = []  # (level, segment, label)
    nodes: List[NodeDraft] = []

    def iter_block_lines(el: Tag) -> List[str]:
        """
        Extrai texto preservando quebras e devolve uma lista de linhas normalizadas.
        - <p>: usa get_text('\\n') para preservar <br> / quebras internas.
        - <table>: converte em texto com '\\n' entre linhas.
        Heurística MVP: se uma linha for muito grande e contiver vários 'Art.', quebra em múltiplas partes.
        """
        if el.name == "p":
            raw = el.get_text("\n", strip=True)
            normalized = normalize_text_keep_newlines(raw)
        else:
            normalized = normalize_text_keep_newlines(_table_to_text(el))

        if not normalized:
            return []

        out: List[str] = []
        for line in normalized.splitlines():
            if not line:
                continue

            # Heurística: algumas páginas "achatam" vários artigos na mesma linha.
            # Se for uma linha enorme com múltiplos Art., tenta quebrar mantendo o parser atual.
            if len(line) > 2000 and line.lower().count("art.") >= 2:
                parts = [p for p in _art_split_re.split(line) if p and p.strip()]
                for p in parts:
                    p2 = normalize_text(p)
                    if p2:
                        out.append(p2)
                continue

            out.append(line)
        return out

    def current_parent_path() -> Optional[str]:
        if not stack:
            return None
        return "/".join(seg for _, seg, _ in stack)

    def push_heading(level: int, label: str) -> NodeDraft:
        nonlocal stack
        seg = f"l{level}-{slugify(label, max_len=80)}"

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
            text_normalized="",
        )

    # preâmbulo: tudo antes de começar CAP/ANEXO/Art.
    preamble_parts: List[str] = []
    in_preamble = True

    # texto solto fora de artigo/anexo (ex: “PUB D.O.U...” no final)
    orphan_parts: List[str] = []

    # artigo em construção
    art_heading: Optional[str] = None
    art_num_current: Optional[str] = None
    art_path: Optional[str] = None
    art_parent: Optional[str] = None
    art_parts: List[str] = []
    
    # Dedup de artigos: (art_num, text_hash) -> index em nodes
    seen_articles: dict[tuple[str, str], int] = {}


    # anexo em construção (conteúdo do anexo)
    annex_heading: Optional[str] = None
    annex_path: Optional[str] = None
    annex_parent: Optional[str] = None
    annex_parts: List[str] = []

    # Datalegis às vezes repete o HTML (print/mobile/etc) após os anexos.
    # Se isso acontecer, o parser antigo "grudava" o documento repetido dentro do último anexo.
    # Ao detectar um restart do documento enquanto estamos em anexo, ignoramos o restante.
    ignore_rest = False
    
    # Datalegis às vezes repete o HTML (print/mobile/etc). Evita criar o mesmo ANEXO duas vezes.
    seen_annex_labels: set[str] = set()

    def _canon_label(s: str) -> str:
        return normalize_text(s).casefold()

    # Detecta repetição do documento (print/mobile/duplicação de HTML)
    # Fora de anexo, o Datalegis às vezes replica o ato inteiro após o fim.
    # Se o cabeçalho típico reaparecer, cortamos.
    _DOC_HEADER_MARKERS = (
        "MINISTÉRIO DA SAÚDE",
        "MINISTERIO DA SAUDE",
        "AGÊNCIA NACIONAL DE VIGILÂNCIA SANITÁRIA",
        "AGENCIA NACIONAL DE VIGILANCIA SANITARIA",
        "DIRETORIA COLEGIADA",
        "RESOLUÇÃO",
        "RESOLUCAO",
    )
    seen_doc_header_markers: set[str] = set()

    def _doc_header_marker(line: str) -> Optional[str]:
        t = normalize_text(line)
        tu = t.upper()
        for m in _DOC_HEADER_MARKERS:
            if tu.startswith(m):
                return m
        return None
    
    def _looks_like_doc_restart(line: str) -> bool:
        """
        Heurística: dentro de ANEXO, encontrar 'CAPÍTULO ...' ou cabeçalhos típicos do início do ato
        indica que o site duplicou o documento (print/mobile). Nesse caso, devemos parar.
        """
        t = normalize_text(line)
        if _cap_re.match(t):
            return True
        if t.upper().startswith("MINISTÉRIO DA SAÚDE"):
            return True
        if t.upper().startswith("AGÊNCIA NACIONAL DE VIGILÂNCIA SANITÁRIA"):
            return True
        return False

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

    def flush_orphans() -> None:
        nonlocal orphan_parts
        txt = normalize_text_keep_newlines("\n".join(orphan_parts))
        if txt:
            nodes.append(
                NodeDraft(
                    heading_level=1,
                    heading_text="Notas finais",
                    path="notas-finais",
                    parent_path=None,
                    text_normalized=txt,
                )
            )
        orphan_parts = []

    def flush_article() -> None:
        nonlocal art_heading, art_num_current, art_path, art_parent, art_parts, seen_articles
        if not art_heading or not art_path or not art_num_current:
            art_heading = None
            art_num_current = None
            art_path = None
            art_parent = None
            art_parts = []
            return

        txt = normalize_text_keep_newlines("\n".join(art_parts))
        # chave de duplicidade: mesmo Art. + mesmo conteúdo => escolher o path mais longo
        txt_hash = sha256_hex(normalize_for_hash(txt))
        key = (art_num_current, txt_hash)

        new_node = NodeDraft(
            heading_level=5,
            heading_text=art_heading,
            path=art_path,
            parent_path=art_parent,
            text_normalized=txt,
        )

        if key in seen_articles:
            idx = seen_articles[key]
            old = nodes[idx]
            # mantém o "path longo" (mais profundo)
            old_depth = old.path.count("/") if old.path else 0
            new_depth = new_node.path.count("/") if new_node.path else 0
            if new_depth > old_depth:
                nodes[idx] = new_node
            # se new_depth <= old_depth: descarta o novo (fica o longo/antigo)
        else:
            seen_articles[key] = len(nodes)
            nodes.append(new_node)
            
        art_heading = None
        art_num_current = None
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
        nonlocal art_heading, art_num_current, art_path, art_parent, art_parts
        flush_article()

        parent = current_parent_path()
        seg = f"art-{art_num}"
        art_parent = parent
        art_path = f"{parent}/{seg}" if parent else seg
        art_heading = art_label
        art_num_current = art_num
        art_parts = []
        if remainder:
            art_parts.append(remainder.strip())

    def start_annex(label: str) -> None:
        nonlocal annex_heading, annex_path, annex_parent, annex_parts, seen_annex_labels

        canon = _canon_label(label)

        # 1) Se já estamos dentro do MESMO anexo e ele aparece de novo,
        # trata como conteúdo (não reinicia e não duplica path).
        if annex_heading and canon == _canon_label(annex_heading):
            annex_parts.append(label)
            return

        # 2) Se este anexo já apareceu antes (HTML duplicado), ignora para não duplicar nodes/paths.
        if canon in seen_annex_labels:
            return

        # 3) Fluxo normal: fecha o que estava aberto e inicia novo anexo
        flush_article()
        flush_annex()

        stack.clear()
        hnode = push_heading(1, label)
        nodes.append(hnode)

        annex_heading = label
        annex_parent = hnode.parent_path
        annex_path = hnode.path + "/conteudo"
        annex_parts = []

        seen_annex_labels.add(canon)

    for el in blocks:
        if ignore_rest:
            break
        lines = iter_block_lines(el)
        if not lines:
            continue

        # Só consideramos "heading centralizado" quando o <p> é um único bloco curto.
        # Se tiver múltiplas linhas, tratamos linha-a-linha para favorecer detecção de Art./CAP etc.
        if el.name == "p" and len(lines) == 1 and _looks_like_center_heading(el, lines[0]):
            t = lines[0]
            if _SKIP_LINE_RE.match(t):
                continue

            # Corte de repetição do documento fora de anexo:
            # Se um marcador de cabeçalho típico reaparecer depois do início, paramos.
            mk = _doc_header_marker(t)
            if mk:
                if mk in seen_doc_header_markers and not in_preamble:
                    flush_article()
                    flush_annex()
                    flush_orphans()
                    ignore_rest = True
                    break
                seen_doc_header_markers.add(mk)

            is_anexo = _anexo_re.match(t)
            is_cap = _cap_re.match(t)
            is_sec = _sec_re.match(t)
            is_subsec = _subsec_re.match(t)
            is_art = _art_re.match(t)

            if in_preamble and (is_art or is_cap or is_anexo):
                flush_preamble()

            if in_preamble:
                preamble_parts.append(t)
                continue

            # Se estamos dentro de anexo e apareceu um "restart" típico do documento, corta duplicação.
            if annex_heading and _looks_like_doc_restart(t):
                flush_article()
                flush_annex()
                ignore_rest = True
                continue

            if annex_heading and not is_anexo:
                annex_parts.append(t)
                continue

            if is_anexo:
                start_annex(t)
                continue

            if is_cap:
                flush_article()
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
                hnode = push_heading(4, t)
                nodes.append(hnode)
                continue

            if is_art:
                m = _art_re.match(t)
                assert m is not None
                art_num = m.group(1)
                sym = m.group(2) or ""
                remainder = (m.group(3) or "").strip()
                head = f"Art. {art_num}{sym}".strip()
                start_article(head, art_num, remainder)
                continue

            # headings centrados genéricos (DISPOSIÇÕES..., Objetivos, etc.)
            flush_article()
            last_level = stack[-1][0] if stack else 0
            lvl = 2 if last_level <= 1 else 4
            hnode = push_heading(lvl, t)
            nodes.append(hnode)
            continue

        # Processamento padrão: linha a linha
        for t in lines:
            if not t:
                continue
            if _SKIP_LINE_RE.match(t):
                continue

            # Corte de repetição do documento fora de anexo
            mk = _doc_header_marker(t)
            if mk:
                if mk in seen_doc_header_markers and not in_preamble:
                    flush_article()
                    flush_annex()
                    flush_orphans()
                    ignore_rest = True
                    break
                seen_doc_header_markers.add(mk)

            is_art = _art_re.match(t)
            is_cap = _cap_re.match(t)
            is_sec = _sec_re.match(t)
            is_subsec = _subsec_re.match(t)
            is_anexo = _anexo_re.match(t)

            if in_preamble and (is_art or is_cap or is_anexo):
                flush_preamble()

            if in_preamble:
                preamble_parts.append(t)
                continue

            # Se estamos dentro de anexo e apareceu um "restart" típico do documento, corta duplicação.
            if annex_heading and _looks_like_doc_restart(t):
                flush_article()
                flush_annex()
                ignore_rest = True
                continue

            # se está dentro de anexo, tudo vira conteúdo do anexo (até encontrar novo ANEXO)
            if annex_heading and not is_anexo:
                annex_parts.append(t)
                continue

            if is_anexo:
                start_annex(t)
                continue

            if is_cap:
                flush_article()
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
                hnode = push_heading(4, t)
                nodes.append(hnode)
                continue

            if is_art:
                m = _art_re.match(t)
                assert m is not None
                art_num = m.group(1)
                sym = m.group(2) or ""
                remainder = (m.group(3) or "").strip()
                head = f"Art. {art_num}{sym}".strip()
                start_article(head, art_num, remainder)
                continue

            # conteúdo normal
            if art_heading:
                art_parts.append(t)
            elif annex_heading:
                annex_parts.append(t)
            else:
                orphan_parts.append(t)

    if in_preamble:
        flush_preamble()

    flush_article()
    flush_annex()
    flush_orphans()

    # hash global (inclui heading_text + texto)
    all_for_hash: List[str] = []
    for n in nodes:
        all_for_hash.append(n.heading_text)
        if n.text_normalized:
            all_for_hash.append(n.text_normalized)

    content_hash = sha256_hex(normalize_for_hash("\n".join(all_for_hash)))
    return title, nodes, content_hash


def extract_nodes_auto(
    html: str, max_heading_level: int
) -> tuple[str, List[NodeDraft], str]:
    maybe = extract_nodes_datalegis(html)
    if maybe:
        return maybe
    return extract_nodes_by_headings(html, max_heading_level=max_heading_level)


# --------- DB helpers ---------

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


# --------- CLI ---------

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Ingestão MVP: URL -> nodes (Datalegis ou headings) -> READY_FOR_INDEX + enqueue IndexVersionJob"
    )
    ap.add_argument("--url", required=True)
    ap.add_argument("--source-org", required=True)
    ap.add_argument("--doc-type", required=True)

    ap.add_argument(
        "--max-heading-level",
        type=int,
        default=settings.CANON_MAX_HEADING_LEVEL,
        help="Nível máximo de heading (H1..Hn) quando NÃO for Datalegis.",
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

    # 2) canonicalizar
    title, node_drafts, content_hash = extract_nodes_auto(
        html, max_heading_level=args.max_heading_level
    )

    # Métricas rápidas de qualidade do ingest (MVP)
    text_sizes = [len(nd.text_normalized or "") for nd in node_drafts if (nd.text_normalized or "").strip()]
    text_sizes.sort()
    max_chars = text_sizes[-1] if text_sizes else 0
    p95_chars = 0
    if text_sizes:
        idx = int(math.floor(0.95 * (len(text_sizes) - 1)))
        p95_chars = text_sizes[idx]
    print(
        f"[ingest_web] extracted title={title!r} nodes={len(node_drafts)} "
        f"max_node_chars={max_chars} p95_node_chars={p95_chars}"
    )

    # 3) preparar inserts
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
            "pipeline_version": args.pipeline_version,
            "embedding_model_id": args.embedding_model_id,
        },
    )

    print(
        f"[ingest_web] ok version_id={version_id} document_id={document_id} nodes={len(node_rows)} content_hash={content_hash}"
    )
    print(f"[ingest_web] enqueued IndexVersionJob job_id={job_id}")


if __name__ == "__main__":
    main()
