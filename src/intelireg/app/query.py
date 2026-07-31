from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import uuid4

from intelireg.audit import record_query_run
from intelireg.retrieval import hybrid_retrieve_rrf


def build_query_output(
    *,
    request_id: str,
    run_id: str,
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
    for i, row in enumerate(rows, start=1):
        results.append(
            {
                "rank": i,
                # Campos legados mantidos durante o contrato v1.
                "rrf_score": row["rrf_score"],
                "fts_rank": row["fts_rank"],
                "fts_score": row["fts_score"],
                "vec_rank": row["vec_rank"],
                "vec_distance": row["vec_distance"],
                "scores": {
                    "rrf_score": row["rrf_score"],
                    "fts_rank": row["fts_rank"],
                    "fts_score": row["fts_score"],
                    "vec_rank": row["vec_rank"],
                    "vec_distance": row["vec_distance"],
                },
                "chunk": {
                    "chunk_id": row["chunk_id"],
                    "version_id": row["version_id"],
                    "chunk_index": row["chunk_index"],
                    "tokens_count": row["tokens_count"],
                    "text": row["text"],
                },
                "document": row["document"],
                "citations": row["node_refs"] or [],
            }
        )

    return {
        "schema_version": 1,
        "run_type": "query_rag",
        "request_id": request_id,
        "run_id": run_id,
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
    request_id: Optional[str] = None,
    run_id: Optional[str] = None,
    audit: bool = True,
) -> Dict[str, Any]:
    canonical_request_id = request_id or str(uuid4())
    canonical_run_id = run_id or str(uuid4())

    out = build_query_output(
        request_id=canonical_request_id,
        run_id=canonical_run_id,
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
        selected = [
            {
                "rank": result["rank"],
                "rrf_score": result["scores"]["rrf_score"],
                "chunk_id": result["chunk"]["chunk_id"],
                "version_id": result["chunk"]["version_id"],
                "chunk_index": result["chunk"]["chunk_index"],
            }
            for result in out["results"]
        ]

        record_query_run(
            request_id=canonical_request_id,
            run_id=canonical_run_id,
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
