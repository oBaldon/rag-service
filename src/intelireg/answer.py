from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

_TOKEN_RE = re.compile(r"[a-zA-ZÀ-ÿ0-9]+", re.UNICODE)
_DECIMAL_RE = re.compile(r"\b\d+[\.,]\d+\b")

_STOP = {
    "quais", "qual", "que", "o", "a", "os", "as",
    "um", "uma", "uns", "umas",
    "de", "do", "da", "dos", "das",
    "para", "por", "com", "sem", "em", "no", "na", "nos", "nas",
    "e", "ou",
    "ter", "têm", "tem", "até", "sobre", "como",
    "requisitos", "requisito", "exigencias", "exigência", "exigências",
    "rdc", "lei", "decreto", "portaria", "resolucao", "resolução",
    "numero", "número", "ano",
}


def _keywords(question: str, max_terms: int = 12) -> List[str]:
    """Extrai keywords para busca extrativa (MVP).

    - Mantém tokens alfanuméricos (inclui números)
    - Captura também decimais (0,2 / 0.2) como termo próprio
    - Remove stopwords comuns
    """
    q = (question or "").casefold().strip()
    if not q:
        return []

    kws: List[str] = []

    # 1) decimais primeiro (mais discriminativos)
    for m in _DECIMAL_RE.finditer(q):
        dec = m.group(0)
        if dec not in kws:
            kws.append(dec)
        alt = dec.replace(",", ".") if "," in dec else dec.replace(".", ",")
        if alt not in kws:
            kws.append(alt)

    # 2) tokens normais
    tokens = _TOKEN_RE.findall(q)
    for t in tokens:
        if t in _STOP:
            continue
        if t.isdigit():
            # números muito curtos ajudam (0/2), mas são pouco discriminativos
            if len(t) <= 2 and t not in kws:
                kws.append(t)
            continue
        if len(t) >= 3 and t not in kws:
            kws.append(t)

        if len(kws) >= max_terms:
            break

    return kws


def _score_line(line_norm: str, kws: List[str]) -> float:
    if not line_norm or not kws:
        return 0.0

    score = 0.0
    for kw in kws:
        if not kw:
            continue
        if kw.isdigit() and len(kw) <= 2:
            # números curtos: peso menor (evita "0" bater em tudo)
            if re.search(rf"\b{re.escape(kw)}\b", line_norm):
                score += 0.25
        elif re.match(r"^\d+[\.,]\d+$", kw):
            if kw in line_norm:
                score += 2.0
        else:
            if kw in line_norm:
                score += 1.0
    return score


def extractive_answer(question: str, sources: List[Dict[str, Any]]) -> Tuple[str, List[str]]:
    """Gera uma resposta extrativa (snippet) varrendo TODAS as fontes.

    Retorna:
      (answer_text, [source_id])
    """
    if not sources:
        return "", []

    kws = _keywords(question)
    best = None  # (score, rrf, -source_idx, -line_idx)
    best_info = None  # (source, lines, line_idx)

    for si, s in enumerate(sources):
        text = (s.get("text") or "").strip()
        if not text:
            continue

        lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
        if not lines:
            continue

        rrf = float(s.get("rrf_score") or 0.0)

        for li, ln in enumerate(lines):
            sc = _score_line(ln.casefold(), kws)
            if sc <= 0:
                continue

            key = (sc, rrf, -si, -li)
            if best is None or key > best:
                best = key
                best_info = (s, lines, li)

    # fallback: primeira fonte, primeiras linhas
    if best_info is None:
        s0 = sources[0]
        text0 = (s0.get("text") or "").strip()
        lines0 = [ln.rstrip() for ln in text0.splitlines() if ln.strip()]
        snippet = "\n".join(lines0[:4]).strip()
        return snippet, [s0.get("source_id") or "S1"]

    s, lines, li = best_info

    start = max(0, li - 1)
    end = min(len(lines), li + 3)

    # heurística: "Art." + "51." => inclui ambos
    if li >= 1 and lines[li - 1].strip() == "Art." and re.match(r"^\d+\.?\s*$", lines[li].strip()):
        start = li - 1
        end = min(len(lines), li + 3)
        
    elif li >= 2 and re.match(r"^\d+\.?\s*$", lines[li - 1].strip()) and lines[li - 2].strip() == "Art.":
        start = li - 2

    if lines[start].strip() == "Art." and start + 1 < len(lines):
        end = max(end, min(len(lines), start + 2))


    snippet_lines = [ln for ln in lines[start:end] if ln.strip()]
    snippet = "\n".join(snippet_lines).strip()
    if len(snippet) > 1200:
        snippet = snippet[:1200].rstrip() + "…"

    return snippet, [s.get("source_id") or "S1"]
