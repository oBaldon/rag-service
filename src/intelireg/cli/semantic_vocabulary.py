from __future__ import annotations

import argparse
import json

from intelireg.semantic_vocabulary import (
    build_passage_embedding_text,
    concept_governance_snapshot,
    expand_query,
    vocabulary_sources_snapshot,
    vocabulary_summary,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Valida e inspeciona o vocabulário semântico regulatório."
    )
    parser.add_argument(
        "query",
        nargs="?",
        default=None,
        help="Consulta opcional para visualizar expansão semântica.",
    )
    parser.add_argument(
        "--title",
        default="",
        help="Título opcional para simular enriquecimento de passage.",
    )
    parser.add_argument(
        "--doc-type",
        default="norma",
        help="Tipo documental usado com --passage.",
    )
    parser.add_argument(
        "--passage",
        default=None,
        help="Trecho opcional para visualizar o input vetorial enriquecido.",
    )
    parser.add_argument(
        "--process",
        default=None,
        choices=[
            "registro_medicamento",
            "pos_registro",
            "afe",
            "cbpf",
            "pesquisa_clinica",
            "transversal",
        ],
        help="Filtra conceitos pelo processo regulatório.",
    )
    parser.add_argument(
        "--priority",
        default=None,
        choices=["P0", "P1", "P2"],
        help="Filtra conceitos pela prioridade de curadoria.",
    )
    parser.add_argument(
        "--sources",
        action="store_true",
        help="Inclui as fontes oficiais de referência do catálogo.",
    )
    args = parser.parse_args()

    governance = concept_governance_snapshot()
    if args.process:
        governance = [
            item
            for item in governance
            if args.process in item["regulatory_processes"]
        ]
    if args.priority:
        governance = [
            item for item in governance if item["priority"] == args.priority
        ]

    payload = {
        "vocabulary": vocabulary_summary(),
        "concept_governance": governance,
    }
    if args.sources:
        payload["sources"] = vocabulary_sources_snapshot()
    if args.query is not None:
        payload["query_expansion"] = expand_query(args.query).debug_dict(
            include_expanded_query=True
        )
    if args.passage is not None:
        enriched, concepts = build_passage_embedding_text(
            title=args.title,
            doc_type=args.doc_type,
            chunk_text=args.passage,
        )
        payload["passage_enrichment"] = {
            "matched_concepts": list(concepts),
            "embedding_input": enriched,
        }

    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
