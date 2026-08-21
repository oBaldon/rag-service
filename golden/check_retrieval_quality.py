from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import unicodedata

import httpx


def _fold(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return " ".join(value.casefold().split())


def _matches_any(value: str, needles: list[str]) -> bool:
    folded = _fold(value)
    return any(_fold(needle) in folded for needle in needles)


def _case_passes(case: dict, results: list[dict]) -> tuple[bool, int | None]:
    max_rank = int(case.get("max_rank", 3))
    for result in results[:max_rank]:
        title = ((result.get("document") or {}).get("title") or "")
        text = ((result.get("chunk") or {}).get("text") or "")

        title_needles = case.get("expected_title_contains_any") or []
        text_needles = case.get("expected_text_contains_any") or []

        title_ok = not title_needles or _matches_any(title, title_needles)
        text_ok = not text_needles or _matches_any(text, text_needles)
        if title_ok and text_ok:
            return True, result.get("rank")
    return False, None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Executa a suíte T01-T09 contra /v1/rag/query."
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("RAG_BASE_URL", "http://127.0.0.1:8088"),
    )
    parser.add_argument(
        "--cases",
        default=str(Path(__file__).with_name("retrieval_quality_cases.json")),
    )
    parser.add_argument("--top-k", type=int, default=12)
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()

    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    headers = {"Content-Type": "application/json"}
    api_key = os.getenv("RAG_API_KEY", "").strip()
    if api_key:
        headers["X-API-Key"] = api_key

    summary = []
    failures = 0
    with httpx.Client(timeout=args.timeout) as client:
        for case in cases:
            response = client.post(
                args.base_url.rstrip("/") + "/v1/rag/query",
                headers=headers,
                json={"question": case["question"], "top_k": args.top_k},
            )
            if response.status_code != 200:
                failures += 1
                summary.append(
                    {
                        "id": case["id"],
                        "status": "ERROR",
                        "http_status": response.status_code,
                        "body": response.text[:500],
                    }
                )
                continue

            body = response.json()
            passed, rank = _case_passes(case, body.get("results") or [])
            if not passed:
                failures += 1
            summary.append(
                {
                    "id": case["id"],
                    "status": "PASS" if passed else "FAIL",
                    "matched_rank": rank,
                    "strategy_version": (body.get("retrieval") or {}).get("strategy_version"),
                    "identifier": (body.get("retrieval") or {}).get("identifier"),
                    "semantic_expansion": (body.get("retrieval") or {}).get("semantic_expansion"),
                    "top3": [
                        {
                            "rank": item.get("rank"),
                            "title": (item.get("document") or {}).get("title"),
                            "scores": item.get("scores"),
                        }
                        for item in (body.get("results") or [])[:3]
                    ],
                }
            )

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
