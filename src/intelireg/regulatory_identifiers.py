from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import re
import unicodedata
from typing import Optional


def _fold(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.casefold()
    value = value.replace("º", "o").replace("°", "o")
    value = re.sub(r"\s+", " ", value).strip()
    return value


_FAMILY_PATTERNS: tuple[tuple[str, re.Pattern[str], tuple[str, ...]], ...] = (
    (
        "inc",
        re.compile(r"\b(?:instrucao normativa conjunta|inc)\b"),
        ("inc",),
    ),
    (
        "rdc",
        re.compile(r"\b(?:resolucao da diretoria colegiada|rdc)\b"),
        ("rdc",),
    ),
    (
        "in",
        re.compile(r"\b(?:instrucao normativa|in)\b"),
        ("inm", "in"),
    ),
    (
        "prt",
        re.compile(r"\b(?:portaria|prt)\b"),
        ("por", "prt"),
    ),
    (
        "res",
        re.compile(r"\b(?:resolucao|res)\b"),
        ("res",),
    ),
)

_NUMBER_AFTER_FAMILY_RE = re.compile(
    r"(?:\s*[-–—]\s*(?:rdc|in|inc|prt|res))?"
    r"\s*(?:n(?:o|r|umero)?\.?\s*)?"
    r"(?P<number>\d{1,3}(?:\.\d{3})+|\d+)\b"
)
_DATE_RE = re.compile(
    r"\b(?P<day>0?[1-9]|[12]\d|3[01])/"
    r"(?P<month>0?[1-9]|1[0-2])/"
    r"(?P<year>19\d{2}|20\d{2}|21\d{2})\b"
)
_YEAR_AFTER_NUMBER_RE = re.compile(r"/(?P<year>19\d{2}|20\d{2}|21\d{2})\b")


@dataclass(frozen=True)
class RegulatoryIdentifier:
    family: str
    number: int
    publication_date: Optional[date] = None
    year: Optional[int] = None
    doc_types: tuple[str, ...] = ()

    @property
    def canonical(self) -> str:
        suffix = ""
        if self.publication_date:
            suffix = f" de {self.publication_date.strftime('%d/%m/%Y')}"
        elif self.year:
            suffix = f"/{self.year}"
        return f"{self.family.upper()} {self.number}{suffix}"


def parse_regulatory_identifier(value: str) -> Optional[RegulatoryIdentifier]:
    """
    Extrai identificadores regulatórios explícitos.

    A função é deliberadamente conservadora: só reconhece um número quando há
    uma família normativa conhecida (RDC, IN, Portaria/PRT, etc.). Isso evita
    interpretar números de artigo, processo ou dosagem como identificador.
    """

    normalized = _fold(value)
    if not normalized:
        return None

    family: Optional[str] = None
    doc_types: tuple[str, ...] = ()
    family_match: Optional[re.Match[str]] = None

    for candidate_family, pattern, candidate_doc_types in _FAMILY_PATTERNS:
        match = pattern.search(normalized)
        if match:
            family = candidate_family
            doc_types = candidate_doc_types
            family_match = match
            break

    if family is None or family_match is None:
        return None

    number_match = _NUMBER_AFTER_FAMILY_RE.match(normalized, family_match.end())
    if not number_match:
        return None

    number_raw = number_match.group("number").replace(".", "")
    try:
        number = int(number_raw)
    except ValueError:
        return None

    publication_date: Optional[date] = None
    year: Optional[int] = None

    date_match = _DATE_RE.search(normalized)
    if date_match:
        try:
            publication_date = date(
                int(date_match.group("year")),
                int(date_match.group("month")),
                int(date_match.group("day")),
            )
            year = publication_date.year
        except ValueError:
            publication_date = None

    if year is None:
        tail = normalized[number_match.end():]
        year_match = _YEAR_AFTER_NUMBER_RE.search(tail)
        if year_match:
            year = int(year_match.group("year"))

    return RegulatoryIdentifier(
        family=family,
        number=number,
        publication_date=publication_date,
        year=year,
        doc_types=doc_types,
    )


def identifiers_match(
    query_identifier: RegulatoryIdentifier,
    document_identifier: RegulatoryIdentifier,
) -> bool:
    if query_identifier.family != document_identifier.family:
        return False
    if query_identifier.number != document_identifier.number:
        return False

    if query_identifier.publication_date is not None:
        if document_identifier.publication_date is not None:
            return query_identifier.publication_date == document_identifier.publication_date
        return document_identifier.year == query_identifier.publication_date.year

    if query_identifier.year is not None:
        return document_identifier.year == query_identifier.year

    return True


def title_matches_identifier(title: str, identifier: RegulatoryIdentifier) -> bool:
    parsed = parse_regulatory_identifier(title)
    return bool(parsed and identifiers_match(identifier, parsed))


def number_title_variants(number: int) -> tuple[str, ...]:
    """Formas usuais de um número de ato em títulos (1520 e 1.520)."""
    plain = str(number)
    variants = [plain]
    if number >= 1000:
        variants.append(f"{number:,}".replace(",", "."))
    return tuple(dict.fromkeys(variants))


def identifier_debug_dict(
    identifier: Optional[RegulatoryIdentifier],
) -> Optional[dict[str, object]]:
    if identifier is None:
        return None
    return {
        "family": identifier.family,
        "number": identifier.number,
        "date": (
            identifier.publication_date.isoformat()
            if identifier.publication_date
            else None
        ),
        "year": identifier.year,
        "canonical": identifier.canonical,
    }


def family_search_terms(identifier: RegulatoryIdentifier) -> tuple[str, ...]:
    mapping = {
        "rdc": ("rdc", "resolucao da diretoria colegiada"),
        "in": ("in", "instrucao normativa"),
        "inc": ("inc", "instrucao normativa conjunta"),
        "prt": ("prt", "portaria"),
        "res": ("res", "resolucao"),
    }
    return mapping.get(identifier.family, (identifier.family,))
