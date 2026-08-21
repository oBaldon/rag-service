from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from intelireg import settings



class SemanticVocabularyError(RuntimeError):
    pass


def _fold(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"\s+", " ", value.casefold()).strip()
    return value


def _contains_phrase(text_folded: str, phrase: str) -> bool:
    needle = _fold(phrase)
    if not needle:
        return False
    # Fronteiras alfanuméricas evitam que "ich" case dentro de outra palavra.
    pattern = r"(?<![a-z0-9])" + re.escape(needle) + r"(?![a-z0-9])"
    return re.search(pattern, text_folded) is not None


@dataclass(frozen=True)
class SemanticConcept:
    concept_id: str
    label: str
    aliases: tuple[str, ...]
    query_expansions: tuple[str, ...]
    embedding_terms: tuple[str, ...]
    notes: str = ""


@dataclass(frozen=True)
class SemanticVocabulary:
    schema_version: int
    vocabulary_version: str
    language: str
    concepts: tuple[SemanticConcept, ...]
    content_hash: str
    embedding_profile_hash: str


@dataclass(frozen=True)
class ConceptMatch:
    concept_id: str
    label: str
    matched_alias: str


@dataclass(frozen=True)
class QueryExpansion:
    original_query: str
    expanded_query: str
    applied: bool
    vocabulary_version: str | None
    vocabulary_hash: str | None
    matched_concepts: tuple[str, ...]
    matched_aliases: tuple[str, ...]
    added_terms: tuple[str, ...]

    def debug_dict(self, *, include_expanded_query: bool = True) -> dict[str, Any]:
        out: dict[str, Any] = {
            "enabled": bool(settings.SEMANTIC_QUERY_EXPANSION_ENABLED),
            "applied": self.applied,
            "vocabulary_version": self.vocabulary_version,
            "vocabulary_hash": self.vocabulary_hash,
            "matched_concepts": list(self.matched_concepts),
            "matched_aliases": list(self.matched_aliases),
            "added_terms": list(self.added_terms),
        }
        if include_expanded_query:
            out["expanded_query"] = self.expanded_query
        return out


def _validate_string_list(value: Any, *, field: str, concept_id: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise SemanticVocabularyError(
            f"{field} de {concept_id} deve ser uma lista."
        )
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise SemanticVocabularyError(
                f"{field} de {concept_id} contém valor inválido."
            )
        normalized = " ".join(item.split()).strip()
        if len(normalized) > 300:
            raise SemanticVocabularyError(
                f"{field} de {concept_id} contém termo com mais de 300 caracteres."
            )
        if normalized not in result:
            result.append(normalized)
    return tuple(result)


def _load_from_path(path: Path) -> SemanticVocabulary:
    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        raise SemanticVocabularyError(
            f"Vocabulário semântico não pôde ser lido em {path}."
        ) from exc

    try:
        payload = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SemanticVocabularyError(
            f"Vocabulário semântico inválido em {path}: JSON malformado."
        ) from exc

    if not isinstance(payload, dict):
        raise SemanticVocabularyError("Vocabulário semântico deve ser um objeto JSON.")

    allowed_top_level = {
        "schema_version",
        "vocabulary_version",
        "language",
        "description",
        "concepts",
    }
    unknown_top_level = set(payload) - allowed_top_level
    if unknown_top_level:
        raise SemanticVocabularyError(
            "Campos não suportados no vocabulário: "
            + ", ".join(sorted(unknown_top_level))
        )

    schema_version = payload.get("schema_version")
    if schema_version != 1:
        raise SemanticVocabularyError(
            f"schema_version do vocabulário não suportado: {schema_version!r}."
        )

    vocabulary_version = str(payload.get("vocabulary_version") or "").strip()
    if not vocabulary_version:
        raise SemanticVocabularyError("vocabulary_version é obrigatório.")

    language = str(payload.get("language") or "pt-BR").strip() or "pt-BR"
    raw_concepts = payload.get("concepts")
    if not isinstance(raw_concepts, list):
        raise SemanticVocabularyError("concepts deve ser uma lista.")

    concepts: list[SemanticConcept] = []
    seen_ids: set[str] = set()
    allowed_concept_fields = {
        "id",
        "label",
        "enabled",
        "aliases",
        "query_expansions",
        "embedding_terms",
        "notes",
    }
    for raw in raw_concepts:
        if not isinstance(raw, dict):
            raise SemanticVocabularyError("Cada conceito deve ser um objeto.")
        unknown_fields = set(raw) - allowed_concept_fields
        if unknown_fields:
            raise SemanticVocabularyError(
                "Campos não suportados em conceito: "
                + ", ".join(sorted(unknown_fields))
            )
        enabled = raw.get("enabled", True)
        if not isinstance(enabled, bool):
            raise SemanticVocabularyError("enabled deve ser booleano.")

        concept_id = str(raw.get("id") or "").strip()
        if not concept_id or not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", concept_id):
            raise SemanticVocabularyError(
                f"id de conceito inválido: {concept_id!r}."
            )
        if concept_id in seen_ids:
            raise SemanticVocabularyError(
                f"id de conceito duplicado: {concept_id}."
            )
        seen_ids.add(concept_id)

        label = " ".join(str(raw.get("label") or "").split()).strip()
        if not label:
            raise SemanticVocabularyError(f"label é obrigatório em {concept_id}.")
        if len(label) > 300:
            raise SemanticVocabularyError(
                f"label de {concept_id} excede 300 caracteres."
            )

        aliases = _validate_string_list(
            raw.get("aliases"), field="aliases", concept_id=concept_id
        )
        if not aliases:
            raise SemanticVocabularyError(
                f"aliases deve conter ao menos um termo em {concept_id}."
            )

        query_expansions = _validate_string_list(
            raw.get("query_expansions"),
            field="query_expansions",
            concept_id=concept_id,
        )
        embedding_terms = _validate_string_list(
            raw.get("embedding_terms"),
            field="embedding_terms",
            concept_id=concept_id,
        )

        if enabled:
            concepts.append(
                SemanticConcept(
                    concept_id=concept_id,
                    label=label,
                    aliases=aliases,
                    query_expansions=query_expansions,
                    embedding_terms=embedding_terms,
                    notes=str(raw.get("notes") or "").strip(),
                )
            )

    embedding_profile_payload = [
        {
            "id": concept.concept_id,
            "aliases": list(concept.aliases),
            "embedding_terms": list(concept.embedding_terms),
        }
        for concept in concepts
    ]
    embedding_profile_bytes = json.dumps(
        embedding_profile_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    return SemanticVocabulary(
        schema_version=1,
        vocabulary_version=vocabulary_version,
        language=language,
        concepts=tuple(concepts),
        content_hash=hashlib.sha256(raw_bytes).hexdigest(),
        embedding_profile_hash=hashlib.sha256(
            embedding_profile_bytes
        ).hexdigest(),
    )


@lru_cache(maxsize=4)
def _load_cached(path_text: str) -> SemanticVocabulary:
    return _load_from_path(Path(path_text))


def load_semantic_vocabulary() -> SemanticVocabulary:
    return _load_cached(str(Path(settings.SEMANTIC_VOCABULARY_PATH).resolve()))


def clear_semantic_vocabulary_cache() -> None:
    _load_cached.cache_clear()


def match_concepts(
    text: str,
    *,
    vocabulary: SemanticVocabulary | None = None,
) -> list[ConceptMatch]:
    if not settings.SEMANTIC_VOCABULARY_ENABLED:
        return []

    vocabulary = vocabulary or load_semantic_vocabulary()
    folded = _fold(text)
    if not folded:
        return []

    matches: list[ConceptMatch] = []
    for concept in vocabulary.concepts:
        for alias in concept.aliases:
            if _contains_phrase(folded, alias):
                matches.append(
                    ConceptMatch(
                        concept_id=concept.concept_id,
                        label=concept.label,
                        matched_alias=alias,
                    )
                )
                break
    return matches


def expand_query(question: str) -> QueryExpansion:
    original = " ".join((question or "").split()).strip()
    if not original or not settings.SEMANTIC_VOCABULARY_ENABLED:
        return QueryExpansion(
            original_query=original,
            expanded_query=original,
            applied=False,
            vocabulary_version=None,
            vocabulary_hash=None,
            matched_concepts=(),
            matched_aliases=(),
            added_terms=(),
        )

    vocabulary = load_semantic_vocabulary()
    matches = match_concepts(original, vocabulary=vocabulary)
    concepts_by_id = {concept.concept_id: concept for concept in vocabulary.concepts}

    added_terms: list[str] = []
    matched_concepts: list[str] = []
    matched_aliases: list[str] = []
    original_folded = _fold(original)

    for match in matches:
        concept = concepts_by_id[match.concept_id]
        matched_concepts.append(match.concept_id)
        matched_aliases.append(match.matched_alias)
        if not settings.SEMANTIC_QUERY_EXPANSION_ENABLED:
            continue
        for term in concept.query_expansions:
            if len(added_terms) >= settings.SEMANTIC_QUERY_EXPANSION_MAX_TERMS:
                break
            folded_term = _fold(term)
            if not folded_term or folded_term in original_folded:
                continue
            if any(_fold(existing) == folded_term for existing in added_terms):
                continue
            added_terms.append(term)

    if not added_terms:
        expanded = original
    else:
        suffix = " ; ".join(added_terms)
        expanded = f"{original}\nTermos regulatórios relacionados: {suffix}".strip()
        if len(expanded) > settings.SEMANTIC_QUERY_EXPANSION_MAX_CHARS:
            expanded = expanded[: settings.SEMANTIC_QUERY_EXPANSION_MAX_CHARS].rstrip()

    return QueryExpansion(
        original_query=original,
        expanded_query=expanded,
        applied=expanded != original,
        vocabulary_version=vocabulary.vocabulary_version,
        vocabulary_hash=vocabulary.content_hash,
        matched_concepts=tuple(dict.fromkeys(matched_concepts)),
        matched_aliases=tuple(dict.fromkeys(matched_aliases)),
        added_terms=tuple(added_terms),
    )


def concept_ids_for_text(
    text: str,
    *,
    vocabulary: SemanticVocabulary | None = None,
) -> tuple[str, ...]:
    return tuple(
        match.concept_id
        for match in match_concepts(text, vocabulary=vocabulary)
    )


def semantic_concept_coverage(
    query_concepts: Iterable[str],
    candidate_text: str,
    *,
    vocabulary: SemanticVocabulary | None = None,
) -> tuple[float, tuple[str, ...]]:
    query_ids = tuple(dict.fromkeys(str(value) for value in query_concepts if value))
    if not query_ids:
        return 0.0, ()

    candidate_ids = set(
        concept_ids_for_text(candidate_text, vocabulary=vocabulary)
    )
    matched = tuple(value for value in query_ids if value in candidate_ids)
    return len(matched) / len(query_ids), matched


def build_passage_embedding_text(
    *,
    title: str,
    doc_type: str,
    chunk_text: str,
) -> tuple[str, tuple[str, ...]]:
    """
    Enriquecimento somente do input vetorial.

    O texto canônico do chunk não é alterado. O prefixo fornece contexto
    documental e conceitos controlados sem duplicar esse conteúdo no banco.
    """
    original = (chunk_text or "").strip()
    if (
        not settings.SEMANTIC_VOCABULARY_ENABLED
        or not settings.SEMANTIC_PASSAGE_ENRICHMENT_ENABLED
    ):
        return original, ()

    vocabulary = load_semantic_vocabulary()
    matches = match_concepts(
        f"{title}\n{original}",
        vocabulary=vocabulary,
    )
    concepts_by_id = {concept.concept_id: concept for concept in vocabulary.concepts}

    matched_ids: list[str] = []
    semantic_terms: list[str] = []
    for match in matches:
        if match.concept_id in matched_ids:
            continue
        matched_ids.append(match.concept_id)
        concept = concepts_by_id[match.concept_id]
        for term in concept.embedding_terms:
            if term not in semantic_terms:
                semantic_terms.append(term)
            if len(semantic_terms) >= settings.SEMANTIC_PASSAGE_MAX_TERMS:
                break
        if len(semantic_terms) >= settings.SEMANTIC_PASSAGE_MAX_TERMS:
            break

    prefix_parts = []
    clean_title = " ".join((title or "").split()).strip()
    clean_doc_type = " ".join((doc_type or "").split()).strip()
    if clean_title:
        prefix_parts.append(f"Documento: {clean_title}")
    if clean_doc_type:
        prefix_parts.append(f"Tipo: {clean_doc_type}")
    if semantic_terms:
        prefix_parts.append("Conceitos: " + "; ".join(semantic_terms))

    prefix = "\n".join(prefix_parts).strip()
    if len(prefix) > settings.SEMANTIC_PASSAGE_MAX_PREFIX_CHARS:
        prefix = prefix[: settings.SEMANTIC_PASSAGE_MAX_PREFIX_CHARS].rstrip()

    if not prefix:
        return original, tuple(matched_ids)
    return f"{prefix}\nTrecho: {original}".strip(), tuple(matched_ids)



def search_terms_for_concepts(
    concept_ids: Iterable[str],
    *,
    vocabulary: SemanticVocabulary | None = None,
    max_terms: int | None = None,
) -> tuple[str, ...]:
    vocabulary = vocabulary or load_semantic_vocabulary()
    wanted = set(str(value) for value in concept_ids if value)
    limit = max_terms or settings.SEMANTIC_CONCEPT_LOOKUP_MAX_TERMS

    terms: list[str] = []
    for concept in vocabulary.concepts:
        if concept.concept_id not in wanted:
            continue
        for term in (
            *concept.aliases,
            *concept.query_expansions,
            *concept.embedding_terms,
        ):
            folded = _fold(term)
            if len(folded) < 3:
                continue
            if any(_fold(existing) == folded for existing in terms):
                continue
            terms.append(term)
            if len(terms) >= limit:
                return tuple(terms)
    return tuple(terms)


def vocabulary_summary() -> dict[str, Any]:
    vocabulary = load_semantic_vocabulary()
    return {
        "schema_version": vocabulary.schema_version,
        "vocabulary_version": vocabulary.vocabulary_version,
        "language": vocabulary.language,
        "content_hash": vocabulary.content_hash,
        "embedding_profile_hash": vocabulary.embedding_profile_hash,
        "concept_count": len(vocabulary.concepts),
        "concept_ids": [concept.concept_id for concept in vocabulary.concepts],
    }
