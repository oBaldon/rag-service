from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from intelireg.semantic_vocabulary import (
    concept_governance_snapshot,
    expand_query,
    vocabulary_summary,
)


ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = ROOT / "golden" / "semantic_vocabulary_cases.json"


def _coverage_requirement(priority: str) -> tuple[int, int]:
    if priority == "P0":
        return 2, 1
    if priority == "P1":
        return 1, 1
    return 1, 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Executa o golden test do vocabulário semântico regulatório."
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Inclui o resultado individual de todos os casos.",
    )
    args = parser.parse_args()

    payload = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    summary = vocabulary_summary()
    governance = {
        item["id"]: item for item in concept_governance_snapshot()
    }
    expected_version = payload.get("vocabulary_version")

    failures: list[dict] = []
    results: list[dict] = []
    positive_counts: Counter[str] = Counter()
    negative_counts: Counter[str] = Counter()
    priority_totals: Counter[str] = Counter()
    priority_passed: Counter[str] = Counter()
    process_totals: Counter[str] = Counter()
    process_passed: Counter[str] = Counter()

    if expected_version != summary["vocabulary_version"]:
        failures.append(
            {
                "id": "VOCAB-VERSION",
                "reason": (
                    f"golden={expected_version!r}; "
                    f"loaded={summary['vocabulary_version']!r}"
                ),
            }
        )

    for case in payload.get("cases", []):
        case_id = case["id"]
        concept_id = case["concept_id"]
        concept = governance.get(concept_id)
        if concept is None:
            item = {
                "id": case_id,
                "concept_id": concept_id,
                "status": "FAIL",
                "reason": "conceito inexistente/inativo no vocabulário carregado",
            }
            results.append(item)
            failures.append(item)
            continue

        expected_priority = case.get("priority")
        if expected_priority and expected_priority != concept["priority"]:
            item = {
                "id": case_id,
                "concept_id": concept_id,
                "status": "FAIL",
                "reason": (
                    f"priority golden={expected_priority}; "
                    f"vocabulary={concept['priority']}"
                ),
            }
            results.append(item)
            failures.append(item)
            continue

        expansion = expand_query(case["query"])
        matched = concept_id in expansion.matched_concepts
        expected_match = bool(case["expected_match"])

        if expected_match:
            positive_counts[concept_id] += 1
        else:
            negative_counts[concept_id] += 1

        missing_terms = [
            term
            for term in case.get("expected_added_terms", [])
            if term not in expansion.added_terms
        ]
        passed = matched == expected_match and not missing_terms

        item = {
            "id": case_id,
            "concept_id": concept_id,
            "priority": concept["priority"],
            "status": "PASS" if passed else "FAIL",
            "expected_match": expected_match,
            "matched_concepts": list(expansion.matched_concepts),
            "matched_aliases": list(expansion.matched_aliases),
            "missing_added_terms": missing_terms,
        }
        results.append(item)
        if not passed:
            failures.append(item)

        priority_totals[concept["priority"]] += 1
        if passed:
            priority_passed[concept["priority"]] += 1

        processes = case.get("regulatory_processes") or concept["regulatory_processes"]
        for process in processes:
            process_totals[process] += 1
            if passed:
                process_passed[process] += 1

    coverage_failures: list[dict] = []
    for concept_id, concept in governance.items():
        required_positive, required_negative = _coverage_requirement(
            concept["priority"]
        )
        found_positive = positive_counts[concept_id]
        found_negative = negative_counts[concept_id]
        if (
            found_positive < required_positive
            or found_negative < required_negative
        ):
            coverage_failures.append(
                {
                    "id": "VOCAB-COVERAGE",
                    "concept_id": concept_id,
                    "priority": concept["priority"],
                    "positive": found_positive,
                    "negative": found_negative,
                    "required_positive": required_positive,
                    "required_negative": required_negative,
                }
            )

    failures.extend(coverage_failures)

    by_priority = {
        priority: {
            "passed": priority_passed[priority],
            "total": priority_totals[priority],
        }
        for priority in sorted(priority_totals)
    }
    by_process = {
        process: {
            "passed": process_passed[process],
            "total": process_totals[process],
        }
        for process in sorted(process_totals)
    }

    out = {
        "status": "PASS" if not failures else "FAIL",
        "vocabulary_version": summary["vocabulary_version"],
        "schema_version": summary["schema_version"],
        "concept_count": summary["concept_count"],
        "priority_counts": summary.get("priority_counts", {}),
        "case_count": len(results),
        "by_priority": by_priority,
        "by_regulatory_process": by_process,
        "coverage_failures": coverage_failures,
        "failures": failures,
    }
    if args.verbose:
        out["results"] = results

    print(json.dumps(out, ensure_ascii=False, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
