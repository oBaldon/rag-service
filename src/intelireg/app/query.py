from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import uuid4

from intelireg.audit import record_query_run
from intelireg.retrieval import hybrid_retrieve_rrf


def build_query_output(
    *,
    question: str,
    version_id: Optional[str],
    pipeline_version: str,
    embedding_model_id: str,
    n1_fts: int,
    n2_vec: int,
    rrf_k: int,
    top_k: int,
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

    results = []
    for i, r in enumerate(rows, start=1):
        results.append(
            {
                "rank": i,
                "rrf_score": r["rrf_score"],
                "fts_rank": r["fts_rank"],
                "fts_score": r["fts_score"],
                "vec_rank": r["vec_rank"],
                "vec_distance": r["vec_distance"],
                "scores": {
                    "rrf_score": r["rrf_score"],
                    "fts_rank": r["fts_rank"],
                    "fts_score": r["fts_score"],
                    "vec_rank": r["vec_rank"],
                    "vec_distance": r["vec_distance"],
                },
                "chunk": {
                    "chunk_id": r["chunk_id"],
                    "version_id": r["version_id"],
                    "chunk_index": r["chunk_index"],
                    "tokens_count": r["tokens_count"],
                    "text": r["text"],
                },
                "document": r["document"],
                "citations": r["node_refs"] or [],
            }
        )

    return {
        "schema_version": 1,
        "run_type": "query_rag",
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
        "retrieval": {
            "version_id": version_id,
            "pipeline_version": pipeline_version,
            "embedding_model_id": embedding_model_id,
            "n1_fts": n1_fts,
            "n2_vec": n2_vec,
            "rrf_k": rrf_k,
            "top_k": top_k,
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "results": results,
    }


def run_query(
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
    out = build_query_output(
        question=question,
        version_id=version_id,
        pipeline_version=pipeline_version,
        embedding_model_id=embedding_model_id,
        n1_fts=n1_fts,
        n2_vec=n2_vec,
        rrf_k=rrf_k,
        top_k=top_k,
    )

    if audit:
        run_id = str(uuid4())
        selected = []
        for r in out["results"]:
            selected.append(
                {
                    "rank": r["rank"],
                    "rrf_score": r["rrf_score"],
                    "chunk_id": r["chunk"]["chunk_id"],
                    "version_id": r["chunk"]["version_id"],
                    "chunk_index": r["chunk"]["chunk_index"],
                }
            )

        record_query_run(
            run_id=run_id,
            question=out["query"],
            filters=out["filters"],
            retrieval_params=out["retrieval"],
            embedding_model_id=out["retrieval"]["embedding_model_id"],
            pipeline_version=out["retrieval"]["pipeline_version"],
            selected=selected,
            result_json=out,
            insufficient_evidence=(len(out["results"]) == 0),
        )

    return out