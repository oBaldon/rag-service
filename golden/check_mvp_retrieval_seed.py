from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path
import sys
import unicodedata

import httpx


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "golden" / "retrieval_mvp_seed_cases.json"


def _fold(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return " ".join(value.casefold().split())


def _title_matches(title: str, expected_documents: list[str]) -> bool:
    folded = _fold(title)
    return any(_fold(item) in folded for item in expected_documents)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Smoke/golden inicial dos cinco processos MVP. "
            "Valida conceitos ativados e, quando curados, documentos esperados."
        )
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("RAG_BASE_URL", "http://127.0.0.1:8088"),
    )
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument("--top-k", type=int, default=12)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Inclui top3 de cada consulta.",
    )
    args = parser.parse_args()

    payload = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    cases = payload.get("cases") or []

    headers = {"Content-Type": "application/json"}
    api_key = os.getenv("RAG_API_KEY", "").strip()
    if api_key:
        headers["X-API-Key"] = api_key

    failures = 0
    results = []
    process_total: Counter[str] = Counter()
    process_pass: Counter[str] = Counter()
    document_assertions = 0

    with httpx.Client(timeout=args.timeout) as client:
        for case in cases:
            process = case["process"]
            process_total[process] += 1
            response = client.post(
                args.base_url.rstrip("/") + "/v1/rag/query",
                headers=headers,
                json={"question": case["question"], "top_k": args.top_k},
            )

            if response.status_code != 200:
                failures += 1
                results.append(
                    {
                        "id": case["id"],
                        "process": process,
                        "status": "ERROR",
                        "http_status": response.status_code,
                        "body": response.text[:500],
                    }
                )
                continue

            body = response.json()
            expansion = (body.get("retrieval") or {}).get("semantic_expansion") or {}
            matched_concepts = set(expansion.get("matched_concepts") or [])
            expected_concepts = set(case.get("expected_concepts") or [])
            missing_concepts = sorted(expected_concepts - matched_concepts)

            api_results = body.get("results") or []
            nonempty_ok = bool(api_results)

            expected_documents = case.get("expected_documents") or []
            document_ok = True
            matched_document_rank = None
            if expected_documents:
                document_assertions += 1
                max_rank = int(case.get("max_rank", 5))
                document_ok = False
                for item in api_results[:max_rank]:
                    title = ((item.get("document") or {}).get("title") or "")
                    if _title_matches(title, expected_documents):
                        document_ok = True
                        matched_document_rank = item.get("rank")
                        break

            passed = not missing_concepts and nonempty_ok and document_ok
            if passed:
                process_pass[process] += 1
            else:
                failures += 1

            item = {
                "id": case["id"],
                "process": process,
                "status": "PASS" if passed else "FAIL",
                "expected_concepts": sorted(expected_concepts),
                "matched_concepts": sorted(matched_concepts),
                "missing_concepts": missing_concepts,
                "result_count": len(api_results),
                "document_assertion": bool(expected_documents),
                "document_ok": document_ok,
                "matched_document_rank": matched_document_rank,
            }
            if args.verbose:
                item["top3"] = [
                    {
                        "rank": row.get("rank"),
                        "title": (row.get("document") or {}).get("title"),
                    }
                    for row in api_results[:3]
                ]
            results.append(item)

    out = {
        "status": "PASS" if failures == 0 else "FAIL",
        "vocabulary_version": payload.get("vocabulary_version"),
        "review_status": payload.get("review_status"),
        "case_count": len(cases),
        "document_assertion_count": document_assertions,
        "by_process": {
            process: {
                "passed": process_pass[process],
                "total": process_total[process],
            }
            for process in sorted(process_total)
        },
        "failures": [item for item in results if item["status"] != "PASS"],
    }
    if args.verbose:
        out["results"] = results

    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
