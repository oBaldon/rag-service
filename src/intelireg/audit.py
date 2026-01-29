from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List

from intelireg.db import get_conn


def compute_result_hash(result_json: Dict[str, Any]) -> str:
    payload = json.dumps(
        result_json,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def record_query_run(
    *,
    run_id: str,
    question: str,
    filters: Dict[str, Any],
    retrieval_params: Dict[str, Any],
    embedding_model_id: str,
    pipeline_version: str,
    selected: List[Dict[str, Any]],
    result_json: Dict[str, Any],
    insufficient_evidence: bool,
) -> None:
    """
    Registra a execução do query_rag na tabela rag_runs.
    Como ainda não há LLM:
      - llm_model_id = 'none'
      - answer_text = ''
    """
    result_hash = compute_result_hash(result_json)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO rag_runs (
                  run_id,
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
                  'none',
                  %s,
                  %s::jsonb,
                  '',
                  %s,
                  %s::jsonb,
                  %s
                )
                """,
                (
                    run_id,
                    question,
                    json.dumps(filters, ensure_ascii=False, default=str),
                    json.dumps(retrieval_params, ensure_ascii=False, default=str),
                    embedding_model_id,
                    pipeline_version,
                    json.dumps(selected, ensure_ascii=False, default=str),
                    insufficient_evidence,
                    json.dumps(result_json, ensure_ascii=False, default=str),
                    result_hash,
                ),
            )
        conn.commit()
