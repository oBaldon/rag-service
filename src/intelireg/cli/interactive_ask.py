from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from intelireg import settings
from intelireg.answer import extractive_answer
from intelireg.retrieval import hybrid_retrieve_rrf
from intelireg.rag_runs import insert_rag_run

_WARNED_VEC = False
_LAST_RUN: Optional[Dict[str, Any]] = None
_LAST_PATH: Optional[Path] = None

def ensure_runs_dir() -> Path:
    runs = Path("storage") / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    return runs


def make_run_path(kind: str, out_dir: Optional[str] = None) -> Path:
    runs_dir = Path(out_dir) if out_dir else ensure_runs_dir()
    runs_dir.mkdir(parents=True, exist_ok=True)
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    rid = uuid.uuid4().hex[:8]
    return runs_dir / f"{day}_{rid}_{kind}.json"

def maybe_warn_vec_once(n2_vec: int) -> None:
    global _WARNED_VEC
    if n2_vec and n2_vec > 0 and not _WARNED_VEC:
        print(
            "[warn] n2-vec > 0: busca vetorial habilitada. "
            "Isso pode mudar o ranking (e consumir mais CPU). "
            "Use n2-vec=0 para modo FTS-only.",
            file=sys.stderr,
        )
        _WARNED_VEC = True
 
def print_header(args: argparse.Namespace) -> None:
    print("InteliReg — CLI interativo (ask)")
    print("Digite a pergunta e pressione Enter.")
    print("Comandos: /exit  /help  /params  /last  /sources  /json")
    print("")
    print("Parâmetros ativos:")
    print(
        f"  pipeline_version={args.pipeline_version} | embedding_model_id={args.embedding_model_id}\n"
        f"  n1_fts={args.n1_fts} | n2_vec={args.n2_vec} | rrf_k={args.rrf_k} | top_k={args.top_k}\n"
        f"  version_id={args.version_id}\n"
    )
    maybe_warn_vec_once(args.n2_vec)

def print_help() -> None:
    print(
        "Comandos:\n"
        "  /exit   sai\n"
        "  /help   mostra esta ajuda\n"
        "  /params mostra os parâmetros atuais\n"
        "  /last   mostra o último run salvo (path + preview)\n"
        "  /sources lista as fontes do último run (ranks/scores)\n"
        "  /json   imprime o JSON do último run\n"
        "  /set n2 <int>     (ex: /set n2 0 | /set n2 10)\n"
        "  /set topk <int>   (ex: /set topk 5)\n"
        "  /set n1 <int>     (ex: /set n1 50)\n"
        "  /set rrf <int>    (ex: /set rrf 60)\n"
        "  /set vid <uuid|none>\n"
        "\n"
        "Dica: se n2_vec > 0, o modelo de embeddings será carregado na primeira query\n"
        "e reaproveitado nas próximas (no mesmo processo).\n"
    )


def print_params(args: argparse.Namespace) -> None:
    print(
        "params:\n"
        f"  pipeline_version={args.pipeline_version}\n"
        f"  embedding_model_id={args.embedding_model_id}\n"
        f"  version_id={args.version_id}\n"
        f"  n1_fts={args.n1_fts}\n"
        f"  n2_vec={args.n2_vec}\n"
        f"  rrf_k={args.rrf_k}\n"
        f"  top_k={args.top_k}\n"
    )

def _need_last() -> bool:
    global _LAST_RUN, _LAST_PATH
    if not _LAST_RUN or not _LAST_PATH:
        print("Nenhum run ainda. Faça uma pergunta primeiro.\n")
        return False
    return True

def print_last() -> None:
    global _LAST_RUN, _LAST_PATH
    if not _need_last():
        return
    ans = (_LAST_RUN.get("answer") or {}).get("text") or ""
    preview = ans.strip().replace("\n", " ")
    if len(preview) > 180:
        preview = preview[:180] + "..."
    print(f"last_run: {_LAST_PATH}")
    print(f"preview: {preview}\n")

def print_sources() -> None:
    global _LAST_RUN
    if not _need_last():
        return
    sources = _LAST_RUN.get("sources") or []
    if not sources:
        print("Sem sources no último run.\n")
        return
    print("sources:")
    for s in sources:
        sid = s.get("source_id")
        doc = s.get("document") or {}
        title = doc.get("title") or ""
        rrf = s.get("rrf_score")
        fts_r = s.get("fts_rank")
        vec_r = s.get("vec_rank")
        vec_d = s.get("vec_distance")
        # primeira “citação” (heading/path) se existir
        cits = s.get("citations") or []
        head = ""
        if cits:
            head = f" | {cits[0].get('heading','')} ({cits[0].get('path','')})"
        print(f"  {sid} | rrf={rrf} fts={fts_r} vec={vec_r} d={vec_d} | {title}{head}")
    print("")

def print_last_json() -> None:
    global _LAST_RUN
    if not _need_last():
        return
    print(json.dumps(_LAST_RUN, ensure_ascii=False, indent=2))
    print("")

def build_sources(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for i, r in enumerate(rows, start=1):
        sid = f"S{i}"
        doc = r["document"]
        sources.append(
            {
                "source_id": sid,
                "document": {
                    "title": doc.get("title"),
                    "source_org": doc.get("source_org"),
                    "doc_type": doc.get("doc_type"),
                    "final_url": doc.get("final_url"),
                    "captured_at": doc.get("captured_at"),
                },
                "chunk": {
                    "chunk_id": r.get("chunk_id"),
                    "version_id": r.get("version_id"),
                    "chunk_index": r.get("chunk_index"),
                    "tokens_count": r.get("tokens_count"),
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
    return sources


def one_ask(args: argparse.Namespace, question: str) -> tuple[Path, Dict[str, Any]]:
    maybe_warn_vec_once(args.n2_vec)

    rows = hybrid_retrieve_rrf(
        question=question,
        pipeline_version=args.pipeline_version,
        embedding_model_id=args.embedding_model_id,
        n1_fts=args.n1_fts,
        n2_vec=args.n2_vec,
        rrf_k=args.rrf_k,
        top_k=args.top_k,
        version_id=args.version_id,
    )

    sources = build_sources(rows)
    answer_text, cited = extractive_answer(question, sources)

    out: Dict[str, Any] = {
        "query": question,
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
            "text": answer_text,
            "cited_sources": cited,
            "mode": "extractive",
        },
        "sources": sources,
    }

    out_path = make_run_path("ask", out_dir=args.out_dir)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    # Micropasso Etapa 6: persiste também no Postgres (rag_runs) com o MESMO JSON
    run_id = insert_rag_run(out)
    if run_id:
        print(f"[db] rag_runs.run_id={run_id}", file=sys.stderr)


    # imprime resposta
    print(answer_text)
    if cited:
        print("\nCitações:", ", ".join(cited))
        for s in sources:
            if s["source_id"] in cited:
                print(f"- {s['source_id']}: {s['document']['title']} ({s['document']['final_url']})")
    print(str(out_path))
    print("")

    return out_path, out


def main() -> None:
    ap = argparse.ArgumentParser(description="CLI interativo (ask): várias perguntas no mesmo processo")
    ap.add_argument("--version-id", default=None)
    ap.add_argument("--pipeline-version", default=settings.PIPELINE_VERSION)
    ap.add_argument("--embedding-model-id", default=settings.EMBEDDING_MODEL_ID)

    ap.add_argument("--n1-fts", type=int, default=settings.RETRIEVAL_N1)
    ap.add_argument("--n2-vec", type=int, default=settings.RETRIEVAL_N2)
    ap.add_argument("--rrf-k", type=int, default=settings.RRF_K)
    ap.add_argument("--top-k", type=int, default=settings.TOP_K_DEFAULT)

    ap.add_argument("--out-dir", default=None, help="Diretório para salvar runs (default: storage/runs)")
    args = ap.parse_args()

    print_header(args)

    while True:
        try:
            q = input("> ").strip()
        except EOFError:
            print("")
            return
        except KeyboardInterrupt:
            print("\n")
            return

        if not q:
            continue
        if q in {"/exit", "/quit"}:
            return
        if q in {"/help", "help"}:
            print_help()
            continue
        if q in {"/params"}:
            print_params(args)
            continue
        if q in {"/last"}:
            print_last()
            continue
        if q in {"/sources"}:
            print_sources()
            continue
        if q in {"/json"}:
            print_last_json()
            continue

        # /set <param> <value>
        if q.startswith("/set "):
            parts = q.split()
            if len(parts) != 3:
                print("Uso: /set n2|topk|n1|rrf|vid <valor>\n")
                continue
            key, val = parts[1], parts[2]
            prev_n2 = int(args.n2_vec)
            try:
                if key == "n2":
                    args.n2_vec = int(val)
                    # se ligou vetorial agora, mostra warn novamente
                    if prev_n2 == 0 and args.n2_vec > 0:
                        global _WARNED_VEC
                        _WARNED_VEC = False
                        maybe_warn_vec_once(args.n2_vec)
                elif key == "topk":
                    args.top_k = int(val)
                elif key == "n1":
                    args.n1_fts = int(val)
                elif key == "rrf":
                    args.rrf_k = int(val)
                elif key == "vid":
                    args.version_id = None if val.lower() == "none" else val
                else:
                    print("Parâmetro desconhecido. Use: n2, topk, n1, rrf, vid\n")
                    continue
            except ValueError:
                print("Valor inválido.\n")
                continue
            print_params(args)
            continue

        out_path, out = one_ask(args, q)
        global _LAST_RUN, _LAST_PATH
        _LAST_RUN = out
        _LAST_PATH = out_path


if __name__ == "__main__":
    main()