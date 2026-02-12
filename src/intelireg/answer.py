from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

_TOKEN_RE = re.compile(r"[a-zA-ZÀ-ÿ0-9]+", re.UNICODE)
_ART_HEADING_RE = re.compile(r"^Art\.\s*\d+", re.IGNORECASE)
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)?%?", re.UNICODE)

# bem mínimo (só pra não “vencer” por ruído)
_STOPWORDS = {
    "quais", "qual", "que", "para", "por", "com", "sem",
    "sobre", "como", "quando", "onde",
    "dos", "das", "do", "da", "de", "em", "no", "na", "nos", "nas",
    "um", "uma", "uns", "umas",
}


def _keywords(question: str, max_terms: int = 10) -> List[str]:
    q = question or ""

    # 1) palavras: agora aceita >= 3 (pra pegar siglas tipo "THC")
    toks = [t.casefold() for t in _TOKEN_RE.findall(q)]
    toks = [t for t in toks if len(t) >= 3 and t not in _STOPWORDS]

    # 2) números/decimais/percentuais: ex. "0,2" / "0,2%" / "0.2"
    nums = [n.casefold() for n in _NUM_RE.findall(q)]
    toks.extend(nums)

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
 
    # sem keywords: devolve um preview honesto
    if not kws:
        preview = "\n".join(lines[:6])
        return preview, ["S1"]

    # score por ocorrência de keywords
    scored = []
    for i, ln in enumerate(lines):
        ln_cf = ln.casefold()
        score = sum(1 for k in kws if k in ln_cf)
        scored.append((score, i, ln))

    # IMPORTANTE: em empate, preferir a PRIMEIRA linha (i menor),
    # evitando “cair” no fim do chunk por causa do reverse sort.
    # Ordenação determinística:
    # - maior score primeiro
    # - em empate, menor índice primeiro (pega a ocorrência mais cedo no chunk)
    scored.sort(key=lambda t: (-t[0], t[1]))
    top_score, top_i, _ = scored[0]
    
    # se nada bate, devolve o começo do chunk (mais “honesto”)
    if top_score == 0:
        preview = "\n".join(lines[:6])
        return preview, ["S1"]

    # pega janela ao redor da melhor linha, mas tenta “ancorar” em um Art. acima
    start = max(0, top_i - 1)
    for j in range(top_i, max(-1, top_i - 4), -1):  # olha até 3 linhas acima
        if _ART_HEADING_RE.match(lines[j]):
            start = j
            break
    end = min(len(lines), start + 3)  # 1–3 linhas no total
    snippet = "\n".join(lines[start:end])

    # resposta simples: snippet + referência S1
    return snippet, ["S1"]
