from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

_TOKEN_RE = re.compile(r"[a-zA-ZÀ-ÿ0-9]+", re.UNICODE)


def _keywords(question: str, max_terms: int = 10) -> List[str]:
    toks = [t.casefold() for t in _TOKEN_RE.findall(question or "")]
    # remove curtas, mantém só “boas”
    toks = [t for t in toks if len(t) >= 4]
    # dedup preservando ordem
    seen = set()
    out = []
    for t in toks:
        if t not in seen:
            seen.add(t)
            out.append(t)
        if len(out) >= max_terms:
            break
    return out


def extractive_answer(question: str, rows: List[Dict[str, Any]]) -> Tuple[str, List[str]]:
    """
    MVP sem LLM: pega o melhor chunk e retorna 1–3 linhas mais relevantes.
    Retorna (answer, cited_source_ids) onde source_id é "S1", "S2"...
    """
    if not rows:
        return "Não encontrei evidência suficiente no corpus indexado.", []

    kws = _keywords(question)
    # usa top-1 por RRF, mas tenta achar linhas “boas”
    best = rows[0]
    text = (best.get("text") or "").strip()
    if not text:
        return "Não encontrei evidência suficiente no corpus indexado.", []

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return "Não encontrei evidência suficiente no corpus indexado.", []

    # score por ocorrência de keywords
    scored = []
    for i, ln in enumerate(lines):
        ln_cf = ln.casefold()
        score = sum(1 for k in kws if k in ln_cf)
        scored.append((score, i, ln))

    scored.sort(reverse=True)  # maior score primeiro
    top_score, top_i, _ = scored[0]

    # se nada bate, devolve o começo do chunk (mais “honesto”)
    if top_score == 0:
        preview = "\n".join(lines[:6])
        return preview, ["S1"]

    # pega janela ao redor da melhor linha
    start = max(0, top_i - 1)
    end = min(len(lines), top_i + 2)
    snippet = "\n".join(lines[start:end])

    # resposta simples: snippet + referência S1
    return snippet, ["S1"]
