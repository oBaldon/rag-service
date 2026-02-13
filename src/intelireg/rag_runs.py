from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from intelireg.db import get_conn


def _canonical_json(obj: Any) -> str:
    # Determinístico => bom para hash / dedup no futuro
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def insert_rag_run(run: Dict[str, Any]) -> Optional[str]:
    """
    Insere 1 linha em rag_runs reaproveitando exatamente o JSON já produzido pelo CLI.
    Não altera o JSON salvo em storage/runs.
    Retorna run_id (uuid) ou None se falhar.
    """
    question = run.get("query") or ""
    filters = run.get("filters") or {}
    params = run.get("params") or {}
    answer_obj = run.get("answer") or {}

    embedding_model_id = (filters.get("embedding_model_id") or "unknown").strip()
    pipeline_version = (filters.get("pipeline_version") or "unknown").strip()
    answer_text = answer_obj.get("text") or ""
    cited_sources = answer_obj.get("cited_sources") or []
    sources = run.get("sources") or []

    cited_set = set(cited_sources) if isinstance(cited_sources, list) else set()
    selected = [s for s in sources if s.get("source_id") in cited_set] if cited_set else []

    low = (answer_text or "").casefold()
    insufficient_evidence = ("não encontrei evidência" in low) or ("nao encontrei evidencia" in low)

    asked_at = datetime.now(timezone.utc)

    result_json_text = _canonical_json(run)
    result_hash = _sha256_hex(result_json_text)

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO rag_runs (
                      asked_at,
                      question,
                      filters,
                      retrieval_params,
                      embedding_model_id,
                      llm_model_id,
                      pipeline_version,
                      selected,
                      answer_text,
                      insufficient_evidence,
                      result_json,
                      result_hash
                    )
                    VALUES (
                      %s,
                      %s,
                      %s::jsonb,
                      %s::jsonb,
                      %s,
                      %s,
                      %s,
                      %s::jsonb,
                      %s,
                      %s,
                      %s::jsonb,
                      %s
                    )
                    RETURNING run_id
                    """,
                    (
                        asked_at,
                        question,
                        json.dumps(filters, ensure_ascii=False),
                        json.dumps(params, ensure_ascii=False),
                        embedding_model_id,
                        "extractive",  # sem LLM no MVP (ajuste se quiser)
                        pipeline_version,
                        json.dumps(selected, ensure_ascii=False),
                        answer_text,
                        bool(insufficient_evidence),
                        result_json_text,
                        result_hash,
                    ),
                )
                run_id = cur.fetchone()[0]
            conn.commit()
        return str(run_id)
    except Exception as e:
        # Não quebra o CLI por causa do DB (micropasso = ROI sem dor)
        print(f"[warn] falha ao inserir em rag_runs: {e}", file=sys.stderr)
        return None
