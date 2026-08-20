from __future__ import annotations

import argparse
import json
from typing import Any

from intelireg import settings
from intelireg.app.query import run_query


def _compact_result(result: dict[str, Any]) -> dict[str, Any]:
    scores = result.get("scores") or {}
    document = result.get("document") or {}
    chunk = result.get("chunk") or {}
    return {
        "rank": result.get("rank"),
        "title": document.get("title"),
        "document_id": document.get("document_id"),
        "version_id": chunk.get("version_id"),
        "chunk_index": chunk.get("chunk_index"),
        "scores": {
            "rrf": scores.get("rrf_score"),
            "final": scores.get("final_score"),
            "fts_rank": scores.get("fts_rank"),
            "fts_score": scores.get("fts_score"),
            "vec_rank": scores.get("vec_rank"),
            "vec_distance": scores.get("vec_distance"),
            "lexical_coverage": scores.get("lexical_coverage"),
            "exact_identifier_match": scores.get("exact_identifier_match"),
            "exact_identifier_rank": scores.get("exact_identifier_rank"),
        },
        "text_excerpt": (chunk.get("text") or "")[:500],
    }


def _run(
    *,
    question: str,
    top_k: int,
    n1_fts: int,
    n2_vec: int,
    rrf_k: int,
    version_id: str | None,
) -> dict[str, Any]:
    return run_query(
        question=question,
        version_id=version_id,
        pipeline_version=settings.PIPELINE_VERSION,
        embedding_model_id=settings.EMBEDDING_MODEL_ID,
        n1_fts=n1_fts,
        n2_vec=n2_vec,
        rrf_k=rrf_k,
        top_k=top_k,
        audit=False,
    )


def _compact_output(output: dict[str, Any]) -> dict[str, Any]:
    return {
        "retrieval": output["retrieval"],
        "results": [_compact_result(item) for item in output["results"]],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Diagnóstico local do ranking RAG sem persistir rag_run."
    )
    parser.add_argument("question", help="Pergunta a diagnosticar")
    parser.add_argument("--top-k", type=int, default=settings.TOP_K_DEFAULT)
    parser.add_argument("--n1-fts", type=int, default=settings.RETRIEVAL_N1)
    parser.add_argument("--n2-vec", type=int, default=settings.RETRIEVAL_N2)
    parser.add_argument("--rrf-k", type=int, default=settings.RRF_K)
    parser.add_argument("--version-id", default=None)
    parser.add_argument(
        "--compare-channels",
        action="store_true",
        help="Inclui rankings FTS-only e vector-only sem reranking.",
    )
    args = parser.parse_args()

    hybrid = _run(
        question=args.question,
        top_k=args.top_k,
        n1_fts=args.n1_fts,
        n2_vec=args.n2_vec,
        rrf_k=args.rrf_k,
        version_id=args.version_id,
    )

    payload: dict[str, Any] = {
        "query": hybrid["query"],
        "hybrid": _compact_output(hybrid),
    }

    if args.compare_channels:
        original_rerank = settings.RERANK_ENABLED
        original_diversity = settings.RERANK_DIVERSITY_ENABLED
        try:
            # Diagnóstico cru dos canais: não queremos que cobertura lexical
            # altere a leitura de FTS/vector individualmente.
            settings.RERANK_ENABLED = False
            settings.RERANK_DIVERSITY_ENABLED = False

            fts = _run(
                question=args.question,
                top_k=args.top_k,
                n1_fts=max(1, args.n1_fts),
                n2_vec=0,
                rrf_k=args.rrf_k,
                version_id=args.version_id,
            )
            vec = _run(
                question=args.question,
                top_k=args.top_k,
                n1_fts=0,
                n2_vec=max(1, args.n2_vec),
                rrf_k=args.rrf_k,
                version_id=args.version_id,
            )
            payload["fts_only"] = _compact_output(fts)
            payload["vector_only"] = _compact_output(vec)
        finally:
            settings.RERANK_ENABLED = original_rerank
            settings.RERANK_DIVERSITY_ENABLED = original_diversity

    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
