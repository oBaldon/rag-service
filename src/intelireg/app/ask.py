from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional
import uuid

from intelireg.answer import extractive_answer
from intelireg.retrieval import hybrid_retrieve_rrf
from intelireg.rag_runs import insert_rag_run


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
    audit: bool = True,
) -> Dict[str, Any]:
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
    for i, r in enumerate(rows, start=1):
        sources.append(
            {
                "sid": f"S{i}",
                "chunk_id": r["chunk_id"],
                "version_id": r["version_id"],
                "chunk_index": r["chunk_index"],
                "text": r["text"],
                "document": r["document"],
                "citations": r["node_refs"] or [],
                "scores": {
                    "rrf_score": r["rrf_score"],
                    "fts_rank": r["fts_rank"],
                    "fts_score": r["fts_score"],
                    "vec_rank": r["vec_rank"],
                    "vec_distance": r["vec_distance"],
                },
            }
        )

    # extractive_answer (no MVP) pode retornar tuple (text, cited_sources)
    # ou string/dict dependendo da implementação. Normalizamos para dict
    # para compatibilizar com rag_runs.insert_rag_run().
    raw_answer = extractive_answer(question, sources)
    if isinstance(raw_answer, tuple) and len(raw_answer) >= 1:
        answer_text = raw_answer[0] or ""
        cited_sources = raw_answer[1] if len(raw_answer) > 1 else []
        answer = {"text": answer_text, "cited_sources": cited_sources}
    elif isinstance(raw_answer, str):
        answer = {"text": raw_answer, "cited_sources": []}
    elif isinstance(raw_answer, dict):
        answer = raw_answer
    else:
        # fallback ultra defensivo
        answer = {"text": str(raw_answer), "cited_sources": []}

    run_json: Dict[str, Any] = {
        "schema_version": 1,
        "run_type": "ask_rag",
        "run_id": str(uuid.uuid4()),
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
        insert_rag_run(run_json)

    return run_json