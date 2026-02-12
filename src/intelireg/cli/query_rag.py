from __future__ import annotations

import argparse
import sys
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4

from intelireg import settings
from intelireg.audit import record_query_run
from intelireg.retrieval import hybrid_retrieve_rrf
from intelireg.settings import EMBEDDING_MODEL_ID


def ensure_runs_dir() -> Path:
    runs = Path("storage") / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    return runs


def build_output(
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
        "results": results,
    }


def main() -> None:
    ap = argparse.ArgumentParser(
        description="query_rag MVP (sem LLM): retrieval híbrido (FTS + vetorial) + RRF -> JSON"
    )
    ap.add_argument("--q", "--question", dest="question", required=True)
    ap.add_argument("--version-id", default=None, help="Filtra a busca por uma versão específica")
    ap.add_argument("--pipeline-version", default=settings.PIPELINE_VERSION)
    ap.add_argument("--embedding-model-id", default=settings.EMBEDDING_MODEL_ID)

    ap.add_argument("--n1-fts", type=int, default=settings.RETRIEVAL_N1)
    ap.add_argument(
        "--n2-vec",
        type=int,
        default=0,
        help="Número de candidatos da busca vetorial (0 desativa). Default=0 (útil enquanto embeddings são placeholder).",
    )
    ap.add_argument("--rrf-k", type=int, default=settings.RRF_K)
    ap.add_argument("--top-k", type=int, default=settings.TOP_K_DEFAULT)

    ap.add_argument(
        "--out",
        default=None,
        help="Caminho do JSON de saída. Default: storage/runs/<timestamp>_query.json",
    )
    args = ap.parse_args()

    # Aviso operacional: não tenta "detectar fake", só alerta o operador.
    if getattr(args, "n2_vec", 0) > 0:
        print(
            f"[info] n2-vec > 0: busca vetorial habilitada (embedding_model_id={EMBEDDING_MODEL_ID}).",
            file=sys.stderr,
        )

    # run_id primeiro, para poder entrar no nome do arquivo (e ser auditável)
    run_id = str(uuid4())
    run_id_short = run_id.split("-")[0]  # 8 chars

    out = build_output(
        question=args.question,
        version_id=args.version_id,
        pipeline_version=args.pipeline_version,
        embedding_model_id=args.embedding_model_id,
        n1_fts=args.n1_fts,
        n2_vec=args.n2_vec,
        rrf_k=args.rrf_k,
        top_k=args.top_k,
    )

    # Define output path antes de escrever, para registrar no JSON e na auditoria
    runs_dir = ensure_runs_dir()
    if args.out:
        out_path = Path(args.out)
    else:
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        out_path = runs_dir / f"{day}_{run_id_short}_query.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # run_id e metadados úteis (auditáveis)
    out["run_id"] = run_id
    out["output_path"] = str(out_path)

    # escreve JSON
    out_path.write_text(
        json.dumps(out, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    # resumo auditável (ids + scores)
    selected = [
        {
            "rank": r["rank"],
            "chunk_id": r["chunk"]["chunk_id"],
            "version_id": r["chunk"]["version_id"],
            "rrf_score": r["rrf_score"],
            "fts_rank": r["fts_rank"],
            "fts_score": r["fts_score"],
            "vec_rank": r["vec_rank"],
            "vec_distance": r["vec_distance"],
        }
        for r in out["results"]
    ]

    # grava auditoria no rag_runs (llm_model_id = 'none' é tratado no audit.py)
    record_query_run(
        run_id=run_id,
        question=out["query"],
        filters=out["filters"],
        retrieval_params=out["params"],
        embedding_model_id=out["filters"]["embedding_model_id"],
        pipeline_version=out["filters"]["pipeline_version"],
        selected=selected,
        result_json=out,
        insufficient_evidence=(len(out["results"]) == 0),
    )

    print(str(out_path))


if __name__ == "__main__":
    main()
