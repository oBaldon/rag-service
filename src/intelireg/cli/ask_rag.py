from __future__ import annotations

import argparse
import sys
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from intelireg import settings
from intelireg.app.ask import run_ask


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
            f"Isso pode mudar o ranking e consumir mais CPU (embedding_model_id={args.embedding_model_id}).",
            file=sys.stderr,
        )

    run_json = run_ask(
        question=args.question,
        version_id=args.version_id,
        pipeline_version=args.pipeline_version,
        embedding_model_id=args.embedding_model_id,
        n1_fts=args.n1_fts,
        n2_vec=args.n2_vec,
        rrf_k=args.rrf_k,
        top_k=args.top_k,
        audit=True,
    )

    runs_dir = ensure_runs_dir()
    if args.out:
        out_path = Path(args.out)
    else:
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        rid = uuid.uuid4().hex[:8]
        out_path = runs_dir / f"{day}_{rid}_ask.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    out_path.write_text(json.dumps(run_json, ensure_ascii=False, indent=2), encoding="utf-8")
    print(str(out_path))


if __name__ == "__main__":
    main()
