from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict

from intelireg.db import get_conn


def _canonical_json(obj: Any) -> str:
    return json.dumps(
        obj,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def insert_rag_run(run: Dict[str, Any]) -> str:
    """
    Persiste uma execução do endpoint extrativo `/ask`.

    A auditoria é parte do contrato: uma falha de persistência é propagada
    para que a API não devolva uma execução não rastreável como concluída.
    """
    request_id = str(run.get("request_id") or "").strip()
    run_id = str(run.get("run_id") or "").strip()
    if not request_id or not run_id:
        raise ValueError("request_id e run_id são obrigatórios para auditoria")

    question = run.get("query") or ""
    filters = run.get("filters") or {}
    params = run.get("params") or {}
    answer_obj = run.get("answer") or {}

    embedding_model_id = (
        filters.get("embedding_model_id") or "unknown"
    ).strip()
    pipeline_version = (
        filters.get("pipeline_version") or "unknown"
    ).strip()
    answer_text = answer_obj.get("text") or ""
    cited_sources = answer_obj.get("cited_sources") or []
    sources = run.get("sources") or []

    cited_set = set(cited_sources) if isinstance(cited_sources, list) else set()
    selected = (
        [
            source
            for source in sources
            if (source.get("source_id") or source.get("sid")) in cited_set
        ]
        if cited_set
        else []
    )

    normalized_answer = answer_text.casefold()
    insufficient_evidence = (
        "não encontrei evidência" in normalized_answer
        or "nao encontrei evidencia" in normalized_answer
    )

    asked_at = datetime.now(timezone.utc)
    result_json_text = _canonical_json(run)
    result_hash = _sha256_hex(result_json_text)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO rag_runs (
                  request_id,
                  run_id,
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
                    request_id,
                    run_id,
                    asked_at,
                    question,
                    json.dumps(filters, ensure_ascii=False),
                    json.dumps(params, ensure_ascii=False),
                    embedding_model_id,
                    "extractive",
                    pipeline_version,
                    json.dumps(selected, ensure_ascii=False),
                    answer_text,
                    bool(insufficient_evidence),
                    result_json_text,
                    result_hash,
                ),
            )
            persisted_run_id = cur.fetchone()[0]
        conn.commit()

    return str(persisted_run_id)
