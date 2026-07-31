from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import uuid4

from intelireg.answer import extractive_answer
from intelireg.rag_runs import insert_rag_run
from intelireg.retrieval import hybrid_retrieve_rrf


def run_ask(
    *,
    question: str,
    version_id: Optional[str],
    pipeline_version: str,
    embedding_model_id: str,
    n1_fts: int,
    n2_vec: int,
    rrf_k: int,
    top_k: int,
    request_id: Optional[str] = None,
    run_id: Optional[str] = None,
    audit: bool = True,
) -> Dict[str, Any]:
    canonical_request_id = request_id or str(uuid4())
    canonical_run_id = run_id or str(uuid4())

    rows = hybrid_retrieve_rrf(
        question=question,
        pipeline_version=pipeline_version,
        embedding_model_id=embedding_model_id,
        n1_fts=n1_fts,
        n2_vec=n2_vec,
        rrf_k=rrf_k,
        top_k=top_k,
        version_id=version_id,
    )

    sources = []
    for index, row in enumerate(rows, start=1):
        source_id = f"S{index}"
        sources.append(
            {
                "source_id": source_id,
                # Alias legado mantido durante o contrato v1.
                "sid": source_id,
                "chunk_id": row["chunk_id"],
                "version_id": row["version_id"],
                "chunk_index": row["chunk_index"],
                "text": row["text"],
                "document": row["document"],
                "citations": row["node_refs"] or [],
                "scores": {
                    "rrf_score": row["rrf_score"],
                    "fts_rank": row["fts_rank"],
                    "fts_score": row["fts_score"],
                    "vec_rank": row["vec_rank"],
                    "vec_distance": row["vec_distance"],
                },
            }
        )

    raw_answer = extractive_answer(question, sources)
    if isinstance(raw_answer, tuple) and len(raw_answer) >= 1:
        answer_text = raw_answer[0] or ""
        cited_sources = raw_answer[1] if len(raw_answer) > 1 else []
        answer = {"text": answer_text, "cited_sources": cited_sources}
    elif isinstance(raw_answer, str):
        answer = {"text": raw_answer, "cited_sources": []}
    elif isinstance(raw_answer, dict):
        raw_cited = raw_answer.get("cited_sources") or []
        answer = {
            "text": str(raw_answer.get("text") or ""),
            "cited_sources": [
                str(source_id)
                for source_id in raw_cited
                if source_id is not None
            ],
        }
    else:
        answer = {"text": str(raw_answer), "cited_sources": []}

    run_json: Dict[str, Any] = {
        "schema_version": 1,
        "run_type": "ask_rag",
        "request_id": canonical_request_id,
        "run_id": canonical_run_id,
        "query": question,
        "filters": {
            "version_id": version_id,
            "pipeline_version": pipeline_version,
            "embedding_model_id": embedding_model_id,
        },
        "params": {
            "n1_fts": n1_fts,
            "n2_vec": n2_vec,
            "rrf_k": rrf_k,
            "top_k": top_k,
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "answer": answer,
        "sources": sources,
    }

    if audit:
        persisted_run_id = insert_rag_run(run_json)
        if persisted_run_id != canonical_run_id:
            raise RuntimeError(
                "A auditoria do RAG não persistiu o run_id canônico."
            )

    return run_json
