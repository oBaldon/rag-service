from __future__ import annotations

from datetime import date

import pytest

from intelireg.regulatory_identifiers import (
    identifiers_match,
    parse_regulatory_identifier,
    title_matches_identifier,
)


@pytest.mark.parametrize(
    ("raw", "family", "number", "expected_date"),
    [
        (
            "Instrução Normativa - IN nº 352, de 18/03/2025",
            "in",
            352,
            date(2025, 3, 18),
        ),
        (
            "Portaria - PRT nº 1.520, de 17/09/2019",
            "prt",
            1520,
            date(2019, 9, 17),
        ),
        (
            "RDC nº 476, de 10/03/2021",
            "rdc",
            476,
            date(2021, 3, 10),
        ),
        (
            "Resolução da Diretoria Colegiada - RDC no 17, de 28/03/2013",
            "rdc",
            17,
            date(2013, 3, 28),
        ),
        ("RDC 476/2021", "rdc", 476, None),
    ],
)
def test_parse_regulatory_identifier(raw, family, number, expected_date):
    parsed = parse_regulatory_identifier(raw)

    assert parsed is not None
    assert parsed.family == family
    assert parsed.number == number
    assert parsed.publication_date == expected_date
    if expected_date is not None:
        assert parsed.year == expected_date.year


@pytest.mark.parametrize(
    "raw",
    [
        "responsabilidades dos Representantes da Anvisa na Assembleia ICH",
        "Art. 17 trata de autorização de funcionamento",
        "importação excepcional e temporária de medicamento ou vacina",
        "in vitro avaliação 352",
    ],
)
def test_parser_does_not_invent_identifier_without_normative_family(raw):
    assert parse_regulatory_identifier(raw) is None


def test_title_match_accepts_no_or_numero_variants():
    query = parse_regulatory_identifier("Portaria - PRT nº 1.520, de 17/09/2019")
    assert query is not None

    assert title_matches_identifier(
        "Portaria - PRT no 1.520, de 17/09/2019",
        query,
    )


def test_full_date_prevents_same_number_wrong_year_match():
    query = parse_regulatory_identifier("RDC nº 17, de 28/03/2013")
    other = parse_regulatory_identifier("RDC nº 17, de 28/03/2014")

    assert query is not None
    assert other is not None
    assert identifiers_match(query, other) is False
