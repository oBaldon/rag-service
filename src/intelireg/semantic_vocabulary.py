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


SUPPORTED_SCHEMA_VERSIONS = {1, 2, 3}
REGULATORY_PROCESS_IDS = {
    "registro_medicamento",
    "pos_registro",
    "afe",
    "cbpf",
    "pesquisa_clinica",
    "transversal",
}
CONCEPT_LIFECYCLES = {"active", "deprecated"}
CONCEPT_REVIEW_STATUSES = {
    "pending_domain_review",
    "approved",
    "rejected",
}
CONCEPT_PRIORITIES = {"P0", "P1", "P2"}
PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2}



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
class SemanticSource:
    source_id: str
    label: str
    url: str
    retrieved_at: str
    notes: str = ""


@dataclass(frozen=True)
class SemanticConcept:
    concept_id: str
    label: str
    aliases: tuple[str, ...]
    query_expansions: tuple[str, ...]
    embedding_terms: tuple[str, ...]
    domains: tuple[str, ...] = ()
    regulatory_processes: tuple[str, ...] = ()
    priority: str = "P1"
    parent_concepts: tuple[str, ...] = ()
    related_concepts: tuple[str, ...] = ()
    source_refs: tuple[str, ...] = ()
    lifecycle: str = "active"
    review_status: str = "pending_domain_review"
    owner_role: str = ""
    reviewer_role: str = ""
    reviewed_at: str | None = None
    change_ref: str = ""
    notes: str = ""


@dataclass(frozen=True)
class SemanticVocabulary:
    schema_version: int
    vocabulary_version: str
    language: str
    sources: tuple[SemanticSource, ...]
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
        "sources",
        "concepts",
    }
    unknown_top_level = set(payload) - allowed_top_level
    if unknown_top_level:
        raise SemanticVocabularyError(
            "Campos não suportados no vocabulário: "
            + ", ".join(sorted(unknown_top_level))
        )

    schema_version = payload.get("schema_version")
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise SemanticVocabularyError(
            f"schema_version do vocabulário não suportado: {schema_version!r}."
        )

    vocabulary_version = str(payload.get("vocabulary_version") or "").strip()
    if not vocabulary_version:
        raise SemanticVocabularyError("vocabulary_version é obrigatório.")

    language = str(payload.get("language") or "pt-BR").strip() or "pt-BR"

    sources: list[SemanticSource] = []
    source_ids: set[str] = set()
    if schema_version >= 3:
        raw_sources = payload.get("sources")
        if not isinstance(raw_sources, list):
            raise SemanticVocabularyError(
                "sources deve ser uma lista no vocabulário schema v3."
            )
        for raw_source in raw_sources:
            if not isinstance(raw_source, dict):
                raise SemanticVocabularyError("Cada source deve ser um objeto.")
            allowed_source_fields = {
                "id",
                "label",
                "url",
                "retrieved_at",
                "notes",
            }
            unknown_source_fields = set(raw_source) - allowed_source_fields
            if unknown_source_fields:
                raise SemanticVocabularyError(
                    "Campos não suportados em source: "
                    + ", ".join(sorted(unknown_source_fields))
                )

            source_id = str(raw_source.get("id") or "").strip()
            if not re.fullmatch(r"[A-Z0-9][A-Z0-9_.-]{0,63}", source_id):
                raise SemanticVocabularyError(
                    f"id de source inválido: {source_id!r}."
                )
            if source_id in source_ids:
                raise SemanticVocabularyError(
                    f"id de source duplicado: {source_id}."
                )
            source_ids.add(source_id)

            label = " ".join(str(raw_source.get("label") or "").split()).strip()
            url = str(raw_source.get("url") or "").strip()
            retrieved_at = str(raw_source.get("retrieved_at") or "").strip()
            if not label:
                raise SemanticVocabularyError(
                    f"label é obrigatório no source {source_id}."
                )
            if not re.match(r"^https://", url):
                raise SemanticVocabularyError(
                    f"url do source {source_id} deve usar https://."
                )
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", retrieved_at):
                raise SemanticVocabularyError(
                    f"retrieved_at inválido no source {source_id}: {retrieved_at!r}."
                )
            sources.append(
                SemanticSource(
                    source_id=source_id,
                    label=label,
                    url=url,
                    retrieved_at=retrieved_at,
                    notes=str(raw_source.get("notes") or "").strip(),
                )
            )
    elif payload.get("sources") not in (None, []):
        raise SemanticVocabularyError(
            "sources só é suportado a partir do schema_version 3."
        )

    raw_concepts = payload.get("concepts")
    if not isinstance(raw_concepts, list):
        raise SemanticVocabularyError("concepts deve ser uma lista.")

    concepts: list[SemanticConcept] = []
    seen_ids: set[str] = set()
    all_concept_ids: set[str] = set()

    # Pré-valida IDs para permitir referências hierárquicas a conceitos
    # declarados posteriormente no arquivo.
    for raw in raw_concepts:
        if not isinstance(raw, dict):
            raise SemanticVocabularyError("Cada conceito deve ser um objeto.")
        concept_id = str(raw.get("id") or "").strip()
        if not concept_id or not re.fullmatch(
            r"[a-z0-9][a-z0-9_.-]{0,63}", concept_id
        ):
            raise SemanticVocabularyError(
                f"id de conceito inválido: {concept_id!r}."
            )
        if concept_id in all_concept_ids:
            raise SemanticVocabularyError(
                f"id de conceito duplicado: {concept_id}."
            )
        all_concept_ids.add(concept_id)

    allowed_concept_fields = {
        "id",
        "label",
        "enabled",
        "priority",
        "aliases",
        "query_expansions",
        "embedding_terms",
        "domains",
        "regulatory_processes",
        "parent_concepts",
        "related_concepts",
        "source_refs",
        "governance",
        "notes",
    }

    for raw in raw_concepts:
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

        if schema_version >= 2:
            domains = _validate_string_list(
                raw.get("domains"),
                field="domains",
                concept_id=concept_id,
            )
            if not domains:
                raise SemanticVocabularyError(
                    f"domains deve conter ao menos um domínio em {concept_id}."
                )
            for domain in domains:
                if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", domain):
                    raise SemanticVocabularyError(
                        f"domain inválido em {concept_id}: {domain!r}."
                    )

            regulatory_processes = _validate_string_list(
                raw.get("regulatory_processes"),
                field="regulatory_processes",
                concept_id=concept_id,
            )
            if not regulatory_processes:
                raise SemanticVocabularyError(
                    f"regulatory_processes deve conter ao menos um processo em {concept_id}."
                )
            invalid_processes = sorted(
                set(regulatory_processes) - REGULATORY_PROCESS_IDS
            )
            if invalid_processes:
                raise SemanticVocabularyError(
                    f"processos regulatórios inválidos em {concept_id}: "
                    + ", ".join(invalid_processes)
                )

            governance = raw.get("governance")
            if not isinstance(governance, dict):
                raise SemanticVocabularyError(
                    f"governance é obrigatório em conceito schema v2+: {concept_id}."
                )
            governance_allowed = {
                "lifecycle",
                "review_status",
                "owner_role",
                "reviewer_role",
                "reviewed_at",
                "change_ref",
            }
            governance_unknown = set(governance) - governance_allowed
            if governance_unknown:
                raise SemanticVocabularyError(
                    f"governance de {concept_id} contém campos não suportados: "
                    + ", ".join(sorted(governance_unknown))
                )

            lifecycle = str(governance.get("lifecycle") or "").strip()
            if lifecycle not in CONCEPT_LIFECYCLES:
                raise SemanticVocabularyError(
                    f"lifecycle inválido em {concept_id}: {lifecycle!r}."
                )
            review_status = str(governance.get("review_status") or "").strip()
            if review_status not in CONCEPT_REVIEW_STATUSES:
                raise SemanticVocabularyError(
                    f"review_status inválido em {concept_id}: {review_status!r}."
                )
            owner_role = " ".join(
                str(governance.get("owner_role") or "").split()
            ).strip()
            if not owner_role:
                raise SemanticVocabularyError(
                    f"owner_role é obrigatório em {concept_id}."
                )
            reviewer_role = " ".join(
                str(governance.get("reviewer_role") or "").split()
            ).strip()
            reviewed_at_raw = governance.get("reviewed_at")
            reviewed_at = (
                str(reviewed_at_raw).strip()
                if reviewed_at_raw not in (None, "")
                else None
            )
            change_ref = " ".join(
                str(governance.get("change_ref") or "").split()
            ).strip()
            if not change_ref:
                raise SemanticVocabularyError(
                    f"change_ref é obrigatório em {concept_id}."
                )
            if review_status == "approved" and (
                not reviewer_role or not reviewed_at
            ):
                raise SemanticVocabularyError(
                    f"conceito aprovado {concept_id} exige reviewer_role e reviewed_at."
                )
        else:
            domains = ()
            regulatory_processes = ()
            lifecycle = "active"
            review_status = "pending_domain_review"
            owner_role = "legacy"
            reviewer_role = ""
            reviewed_at = None
            change_ref = "schema-v1"

        if schema_version >= 3:
            priority = str(raw.get("priority") or "").strip()
            if priority not in CONCEPT_PRIORITIES:
                raise SemanticVocabularyError(
                    f"priority inválida em {concept_id}: {priority!r}."
                )
            parent_concepts = _validate_string_list(
                raw.get("parent_concepts"),
                field="parent_concepts",
                concept_id=concept_id,
            )
            related_concepts = _validate_string_list(
                raw.get("related_concepts"),
                field="related_concepts",
                concept_id=concept_id,
            )
            source_refs = _validate_string_list(
                raw.get("source_refs"),
                field="source_refs",
                concept_id=concept_id,
            )
            if not source_refs:
                raise SemanticVocabularyError(
                    f"source_refs deve conter ao menos uma fonte em {concept_id}."
                )
            invalid_sources = sorted(set(source_refs) - source_ids)
            if invalid_sources:
                raise SemanticVocabularyError(
                    f"source_refs inválidos em {concept_id}: "
                    + ", ".join(invalid_sources)
                )
            for ref in (*parent_concepts, *related_concepts):
                if ref == concept_id:
                    raise SemanticVocabularyError(
                        f"{concept_id} não pode referenciar a si próprio."
                    )
                if ref not in all_concept_ids:
                    raise SemanticVocabularyError(
                        f"referência de conceito inexistente em {concept_id}: {ref}."
                    )
        else:
            priority = "P1"
            parent_concepts = ()
            related_concepts = ()
            source_refs = ()

        retrieval_active = (
            enabled
            and lifecycle == "active"
            and review_status != "rejected"
        )
        if retrieval_active:
            concepts.append(
                SemanticConcept(
                    concept_id=concept_id,
                    label=label,
                    aliases=aliases,
                    query_expansions=query_expansions,
                    embedding_terms=embedding_terms,
                    domains=domains,
                    regulatory_processes=regulatory_processes,
                    priority=priority,
                    parent_concepts=parent_concepts,
                    related_concepts=related_concepts,
                    source_refs=source_refs,
                    lifecycle=lifecycle,
                    review_status=review_status,
                    owner_role=owner_role,
                    reviewer_role=reviewer_role,
                    reviewed_at=reviewed_at,
                    change_ref=change_ref,
                    notes=str(raw.get("notes") or "").strip(),
                )
            )

    active_ids = {concept.concept_id for concept in concepts}
    if schema_version >= 3:
        for concept in concepts:
            inactive_refs = sorted(
                (
                    set(concept.parent_concepts)
                    | set(concept.related_concepts)
                )
                - active_ids
            )
            if inactive_refs:
                raise SemanticVocabularyError(
                    f"{concept.concept_id} referencia conceito inativo/rejeitado: "
                    + ", ".join(inactive_refs)
                )

        # Detecta ciclos na hierarquia parent_concepts. Relações em
        # related_concepts podem naturalmente ser bidirecionais.
        parents = {
            concept.concept_id: tuple(concept.parent_concepts)
            for concept in concepts
        }
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> None:
            if node in visited:
                return
            if node in visiting:
                raise SemanticVocabularyError(
                    f"ciclo detectado em parent_concepts envolvendo {node}."
                )
            visiting.add(node)
            for parent in parents.get(node, ()):
                visit(parent)
            visiting.remove(node)
            visited.add(node)

        for concept_id in sorted(active_ids):
            visit(concept_id)

    # Um alias exato não pode ativar dois conceitos simultaneamente.
    # O matching é accent/case-insensitive; a colisão é portanto avaliada na
    # mesma forma normalizada usada pelo retrieval.
    alias_owner: dict[str, str] = {}
    for concept in concepts:
        for alias in concept.aliases:
            folded_alias = _fold(alias)
            existing = alias_owner.get(folded_alias)
            if existing is not None and existing != concept.concept_id:
                raise SemanticVocabularyError(
                    "alias semântico ambíguo entre conceitos ativos: "
                    f"{alias!r} -> {existing}, {concept.concept_id}."
                )
            alias_owner[folded_alias] = concept.concept_id

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
        schema_version=int(schema_version),
        vocabulary_version=vocabulary_version,
        language=language,
        sources=tuple(sources),
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
    concepts_by_id = {concept.concept_id: concept for concept in vocabulary.concepts}
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

    # Em consultas que ativam conceito pai e filho, privilegia o termo mais
    # específico. A prioridade P0/P1/P2 desempata conceitos de especificidade
    # semelhante sem tornar o JSON dependente da ordem física dos registros.
    matches.sort(
        key=lambda item: (
            -len(_fold(item.matched_alias)),
            PRIORITY_ORDER.get(
                concepts_by_id[item.concept_id].priority,
                99,
            ),
            item.concept_id,
        )
    )
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
    concepts_by_id = {concept.concept_id: concept for concept in vocabulary.concepts}
    ordered_ids = tuple(
        dict.fromkeys(str(value) for value in concept_ids if value)
    )
    for concept_id in ordered_ids:
        if concept_id not in wanted:
            continue
        concept = concepts_by_id.get(concept_id)
        if concept is None:
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
    review_counts: dict[str, int] = {}
    priority_counts: dict[str, int] = {}
    domains: list[str] = []
    regulatory_processes: list[str] = []
    hierarchy_edges = 0
    related_edges = 0
    sourced_concepts = 0

    for concept in vocabulary.concepts:
        review_counts[concept.review_status] = (
            review_counts.get(concept.review_status, 0) + 1
        )
        priority_counts[concept.priority] = (
            priority_counts.get(concept.priority, 0) + 1
        )
        hierarchy_edges += len(concept.parent_concepts)
        related_edges += len(concept.related_concepts)
        if concept.source_refs:
            sourced_concepts += 1
        for domain in concept.domains:
            if domain not in domains:
                domains.append(domain)
        for process in concept.regulatory_processes:
            if process not in regulatory_processes:
                regulatory_processes.append(process)

    return {
        "schema_version": vocabulary.schema_version,
        "vocabulary_version": vocabulary.vocabulary_version,
        "language": vocabulary.language,
        "content_hash": vocabulary.content_hash,
        "embedding_profile_hash": vocabulary.embedding_profile_hash,
        "concept_count": len(vocabulary.concepts),
        "concept_ids": [concept.concept_id for concept in vocabulary.concepts],
        "priority_counts": priority_counts,
        "review_status_counts": review_counts,
        "pending_domain_review": review_counts.get("pending_domain_review", 0),
        "domains": domains,
        "regulatory_processes": regulatory_processes,
        "source_count": len(vocabulary.sources),
        "sourced_concepts": sourced_concepts,
        "hierarchy_edges": hierarchy_edges,
        "related_edges": related_edges,
    }


def vocabulary_sources_snapshot() -> list[dict[str, Any]]:
    vocabulary = load_semantic_vocabulary()
    return [
        {
            "id": source.source_id,
            "label": source.label,
            "url": source.url,
            "retrieved_at": source.retrieved_at,
            "notes": source.notes or None,
        }
        for source in vocabulary.sources
    ]


def concept_governance_snapshot() -> list[dict[str, Any]]:
    vocabulary = load_semantic_vocabulary()
    return [
        {
            "id": concept.concept_id,
            "label": concept.label,
            "priority": concept.priority,
            "domains": list(concept.domains),
            "regulatory_processes": list(concept.regulatory_processes),
            "parent_concepts": list(concept.parent_concepts),
            "related_concepts": list(concept.related_concepts),
            "source_refs": list(concept.source_refs),
            "lifecycle": concept.lifecycle,
            "review_status": concept.review_status,
            "owner_role": concept.owner_role,
            "reviewer_role": concept.reviewer_role or None,
            "reviewed_at": concept.reviewed_at,
            "change_ref": concept.change_ref,
        }
        for concept in vocabulary.concepts
    ]
