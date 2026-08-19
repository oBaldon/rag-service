from __future__ import annotations

import pytest

from intelireg.cli.ingest_web import resolve_doc_type


@pytest.mark.parametrize(
    ("source_code", "expected"),
    [
        ("RDC", "rdc"),
        ("INM", "inm"),
        ("POR", "por"),
        ("RES", "res"),
        ("INC", "inc"),
        ("PCJ", "pcj"),
        ("PIM", "pim"),
    ],
)
def test_resolve_doc_type_from_anvisa_url(
    source_code: str,
    expected: str,
) -> None:
    url = f"https://anvisa.example/ato?acao=abrir&tipo={source_code}&ano=2025"

    assert resolve_doc_type("auto", url, "Título não utilizado") == expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Instrução Normativa Conjunta nº 1", "inc"),
        ("Instrução Normativa - IN nº 189", "inm"),
        ("Resolução da Diretoria Colegiada - RDC nº 1", "rdc"),
        ("Portaria nº 123", "por"),
        ("Resolução - RE nº 20", "res"),
    ],
)
def test_resolve_doc_type_from_title_when_url_has_no_type(
    title: str,
    expected: str,
) -> None:
    assert resolve_doc_type("auto", "https://anvisa.example/ato", title) == expected


def test_explicit_doc_type_takes_precedence() -> None:
    url = "https://anvisa.example/ato?tipo=RDC"

    assert resolve_doc_type("Parecer", url, "Resolução") == "parecer"


def test_unknown_document_uses_safe_generic_type() -> None:
    assert (
        resolve_doc_type(
            "auto",
            "http://antigo.anvisa.gov.br/legislacao#/visualizar/123",
            "AnvisaLegis",
        )
        == "norma"
    )
