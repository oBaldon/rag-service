from __future__ import annotations

import argparse
import sys
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from intelireg import settings
from intelireg.answer import extractive_answer
from intelireg.retrieval import hybrid_retrieve_rrf


def ensure_runs_dir() -> Path:
    runs = Path("storage") / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    return runs


def main() -> None:
    ap = argparse.ArgumentParser(description="ask_rag MVP: retrieval híbrido -> resposta + citações")
    ap.add_argument("--q", "--question", dest="question", required=True)
    ap.add_argument("--version-id", default=None)
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

    ap.add_argument("--out", default=None, help="JSON saída. Default: storage/runs/<date>_ask.json")
    args = ap.parse_args()
  
    # Aviso operacional: não tenta "detectar fake", só alerta o operador.
    if getattr(args, "n2_vec", 0) > 0:
        print(
            "[warn] n2-vec > 0: busca vetorial habilitada. "
            "Se os embeddings ainda forem placeholder, o ranking pode ficar mais ruidoso. "
            "Use n2-vec=0 para modo FTS-only durante o MVP.",
            file=sys.stderr,
        )

    rows = hybrid_retrieve_rrf(
        question=args.question,
        pipeline_version=args.pipeline_version,
        embedding_model_id=args.embedding_model_id,
        n1_fts=args.n1_fts,
        n2_vec=args.n2_vec,
        rrf_k=args.rrf_k,
        top_k=args.top_k,
        version_id=args.version_id,
    )

    # monta “sources” S1..Sn
    sources = []
    for i, r in enumerate(rows, start=1):
        sid = f"S{i}"
        doc = r["document"]
        sources.append(
            {
                "source_id": sid,
                "document": {
                    "title": doc["title"],
                    "source_org": doc["source_org"],
                    "doc_type": doc["doc_type"],
                    "final_url": doc["final_url"],
                    "captured_at": doc["captured_at"],
                },
                "chunk": {
                    "chunk_id": r["chunk_id"],
                    "version_id": r["version_id"],
                    "chunk_index": r["chunk_index"],
                    "tokens_count": r["tokens_count"],
                },
                "citations": r.get("node_refs") or [],
                "text": r.get("text") or "",
                "rrf_score": r.get("rrf_score"),
                "fts_rank": r.get("fts_rank"),
                "fts_score": r.get("fts_score"),
                "vec_rank": r.get("vec_rank"),
                "vec_distance": r.get("vec_distance"),
            }
        )

    answer, cited = extractive_answer(args.question, sources)

    out: Dict[str, Any] = {
        "query": args.question,
        "filters": {
            "version_id": args.version_id,
            "pipeline_version": args.pipeline_version,
            "embedding_model_id": args.embedding_model_id,
        },
        "params": {
            "n1_fts": args.n1_fts,
            "n2_vec": args.n2_vec,
            "rrf_k": args.rrf_k,
            "top_k": args.top_k,
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "answer": {
            "text": answer,
            "cited_sources": cited,
            "mode": "extractive",
        },
        "sources": sources,
    }

    runs_dir = ensure_runs_dir()
    if args.out:
        out_path = Path(args.out)
    else:
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        out_path = runs_dir / f"{day}_ask.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    # imprime resposta no terminal também
    print(answer)
    if cited:
        print("\nCitações:", ", ".join(cited))
        for s in sources:
            if s["source_id"] in cited:
                print(f"- {s['source_id']}: {s['document']['title']} ({s['document']['final_url']})")

    print(str(out_path))


if __name__ == "__main__":
    main()
