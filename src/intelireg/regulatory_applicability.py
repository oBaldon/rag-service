from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable
from uuid import UUID

from intelireg.db import get_conn


REGULATORY_STATUSES = {
    "vigente",
    "parcialmente_vigente",
    "revogada",
    "suspensa",
    "substituida",
    "sem_efeito",
}

REGULATORY_RELATION_TYPES = {
    "altera",
    "revoga",
    "revoga_parcialmente",
    "substitui",
    "regulamenta",
    "complementa",
    "prorroga",
    "corrige",
    "referencia",
}

REVIEW_STATUSES = {"draft", "approved", "rejected"}

APPROVED_STATUS_BASIS = "approved_curated_assertion"
NO_STATUS_BASIS = "no_approved_curated_assertion"


class RegulatoryApplicabilityError(RuntimeError):
    pass


@dataclass(frozen=True)
class ImportPlan:
    batch_id: str
    status_assertions: tuple[dict[str, Any], ...]
    relations: tuple[dict[str, Any], ...]

    def summary(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "status_assertions": len(self.status_assertions),
            "relations": len(self.relations),
        }


def _iso_date(value: Any, *, field: str) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise RegulatoryApplicabilityError(f"{field} deve usar YYYY-MM-DD.")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise RegulatoryApplicabilityError(
            f"{field} deve usar YYYY-MM-DD: {value!r}."
        ) from exc


def _iso_datetime(value: Any, *, field: str) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise RegulatoryApplicabilityError(f"{field} deve ser ISO-8601.")
    normalized = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise RegulatoryApplicabilityError(
            f"{field} deve ser ISO-8601: {value!r}."
        ) from exc
    if dt.tzinfo is None:
        raise RegulatoryApplicabilityError(
            f"{field} deve conter timezone explícito."
        )
    return dt.isoformat()


def _uuid_text(value: Any, *, field: str, required: bool = True) -> str | None:
    if value in (None, ""):
        if required:
            raise RegulatoryApplicabilityError(f"{field} é obrigatório.")
        return None
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise RegulatoryApplicabilityError(
            f"{field} deve ser UUID válido."
        ) from exc


def _clean_text(
    value: Any,
    *,
    field: str,
    required: bool = False,
    max_length: int = 4000,
) -> str | None:
    if value is None:
        if required:
            raise RegulatoryApplicabilityError(f"{field} é obrigatório.")
        return None
    text = " ".join(str(value).split()).strip()
    if not text:
        if required:
            raise RegulatoryApplicabilityError(f"{field} é obrigatório.")
        return None
    if len(text) > max_length:
        raise RegulatoryApplicabilityError(
            f"{field} excede {max_length} caracteres."
        )
    return text


def _validate_review_fields(item: dict[str, Any], *, prefix: str) -> dict[str, Any]:
    review_status = str(item.get("review_status") or "draft").strip()
    if review_status not in REVIEW_STATUSES:
        raise RegulatoryApplicabilityError(
            f"{prefix}.review_status inválido: {review_status!r}."
        )

    asserted_by = _clean_text(
        item.get("asserted_by"),
        field=f"{prefix}.asserted_by",
        required=True,
        max_length=200,
    )
    reviewed_by = _clean_text(
        item.get("reviewed_by"),
        field=f"{prefix}.reviewed_by",
        max_length=200,
    )
    reviewed_at = _iso_datetime(
        item.get("reviewed_at"),
        field=f"{prefix}.reviewed_at",
    )
    source_url = _clean_text(
        item.get("source_url"),
        field=f"{prefix}.source_url",
        max_length=2000,
    )
    evidence_note = _clean_text(
        item.get("evidence_note"),
        field=f"{prefix}.evidence_note",
        max_length=4000,
    )
    evidence_version_id = _uuid_text(
        item.get("evidence_version_id"),
        field=f"{prefix}.evidence_version_id",
        required=False,
    )

    if review_status == "approved":
        if not reviewed_by or not reviewed_at:
            raise RegulatoryApplicabilityError(
                f"{prefix} aprovado exige reviewed_by e reviewed_at."
            )
        if not (source_url or evidence_note or evidence_version_id):
            raise RegulatoryApplicabilityError(
                f"{prefix} aprovado exige evidência: source_url, "
                "evidence_version_id ou evidence_note."
            )

    return {
        "review_status": review_status,
        "asserted_by": asserted_by,
        "reviewed_by": reviewed_by,
        "reviewed_at": reviewed_at,
        "source_url": source_url,
        "evidence_note": evidence_note,
        "evidence_version_id": evidence_version_id,
    }


def _validate_status_item(raw: Any, index: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise RegulatoryApplicabilityError(
            f"status_assertions[{index}] deve ser objeto."
        )
    prefix = f"status_assertions[{index}]"
    allowed = {
        "assertion_key",
        "document_id",
        "status",
        "effective_from",
        "valid_to",
        "review_status",
        "source_url",
        "evidence_version_id",
        "evidence_note",
        "asserted_by",
        "reviewed_by",
        "reviewed_at",
    }
    unknown = set(raw) - allowed
    if unknown:
        raise RegulatoryApplicabilityError(
            f"{prefix} contém campos não suportados: {', '.join(sorted(unknown))}."
        )

    assertion_key = _clean_text(
        raw.get("assertion_key"),
        field=f"{prefix}.assertion_key",
        required=True,
        max_length=180,
    )
    document_id = _uuid_text(raw.get("document_id"), field=f"{prefix}.document_id")
    status = str(raw.get("status") or "").strip()
    if status not in REGULATORY_STATUSES:
        raise RegulatoryApplicabilityError(
            f"{prefix}.status inválido: {status!r}."
        )
    effective_from = _iso_date(raw.get("effective_from"), field=f"{prefix}.effective_from")
    valid_to = _iso_date(raw.get("valid_to"), field=f"{prefix}.valid_to")
    if effective_from and valid_to and valid_to < effective_from:
        raise RegulatoryApplicabilityError(
            f"{prefix}.valid_to não pode ser anterior a effective_from."
        )

    return {
        "assertion_key": assertion_key,
        "document_id": document_id,
        "status": status,
        "effective_from": effective_from,
        "valid_to": valid_to,
        **_validate_review_fields(raw, prefix=prefix),
    }


def _validate_relation_item(raw: Any, index: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise RegulatoryApplicabilityError(f"relations[{index}] deve ser objeto.")
    prefix = f"relations[{index}]"
    allowed = {
        "relation_key",
        "source_document_id",
        "target_document_id",
        "relation_type",
        "effective_date",
        "scope_note",
        "review_status",
        "source_url",
        "evidence_version_id",
        "evidence_note",
        "asserted_by",
        "reviewed_by",
        "reviewed_at",
    }
    unknown = set(raw) - allowed
    if unknown:
        raise RegulatoryApplicabilityError(
            f"{prefix} contém campos não suportados: {', '.join(sorted(unknown))}."
        )

    relation_key = _clean_text(
        raw.get("relation_key"),
        field=f"{prefix}.relation_key",
        required=True,
        max_length=180,
    )
    source_document_id = _uuid_text(
        raw.get("source_document_id"),
        field=f"{prefix}.source_document_id",
    )
    target_document_id = _uuid_text(
        raw.get("target_document_id"),
        field=f"{prefix}.target_document_id",
    )
    if source_document_id == target_document_id:
        raise RegulatoryApplicabilityError(
            f"{prefix} não pode relacionar um documento a ele mesmo."
        )

    relation_type = str(raw.get("relation_type") or "").strip()
    if relation_type not in REGULATORY_RELATION_TYPES:
        raise RegulatoryApplicabilityError(
            f"{prefix}.relation_type inválido: {relation_type!r}."
        )

    return {
        "relation_key": relation_key,
        "source_document_id": source_document_id,
        "target_document_id": target_document_id,
        "relation_type": relation_type,
        "effective_date": _iso_date(
            raw.get("effective_date"),
            field=f"{prefix}.effective_date",
        ),
        "scope_note": _clean_text(
            raw.get("scope_note"),
            field=f"{prefix}.scope_note",
            max_length=4000,
        ),
        **_validate_review_fields(raw, prefix=prefix),
    }


def validate_import_payload(payload: Any) -> ImportPlan:
    if not isinstance(payload, dict):
        raise RegulatoryApplicabilityError("Arquivo de aplicabilidade deve ser objeto JSON.")

    allowed = {
        "schema_version",
        "batch_id",
        "description",
        "status_assertions",
        "relations",
    }
    unknown = set(payload) - allowed
    if unknown:
        raise RegulatoryApplicabilityError(
            "Campos não suportados no lote: " + ", ".join(sorted(unknown))
        )

    if payload.get("schema_version") != 1:
        raise RegulatoryApplicabilityError("schema_version suportado é 1.")

    batch_id = _clean_text(
        payload.get("batch_id"),
        field="batch_id",
        required=True,
        max_length=180,
    )
    raw_statuses = payload.get("status_assertions", [])
    raw_relations = payload.get("relations", [])
    if not isinstance(raw_statuses, list):
        raise RegulatoryApplicabilityError("status_assertions deve ser lista.")
    if not isinstance(raw_relations, list):
        raise RegulatoryApplicabilityError("relations deve ser lista.")

    statuses = tuple(
        _validate_status_item(item, i)
        for i, item in enumerate(raw_statuses)
    )
    relations = tuple(
        _validate_relation_item(item, i)
        for i, item in enumerate(raw_relations)
    )

    status_keys = [item["assertion_key"] for item in statuses]
    if len(set(status_keys)) != len(status_keys):
        raise RegulatoryApplicabilityError("assertion_key duplicada no lote.")

    relation_keys = [item["relation_key"] for item in relations]
    if len(set(relation_keys)) != len(relation_keys):
        raise RegulatoryApplicabilityError("relation_key duplicada no lote.")

    return ImportPlan(
        batch_id=batch_id or "",
        status_assertions=statuses,
        relations=relations,
    )


def load_import_file(path: str) -> ImportPlan:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise RegulatoryApplicabilityError(
            f"Não foi possível ler JSON de aplicabilidade: {path}."
        ) from exc
    return validate_import_payload(payload)


def _validate_document_references(cur, plan: ImportPlan) -> None:
    document_ids: set[str] = set()
    evidence_version_ids: set[str] = set()
    for item in plan.status_assertions:
        document_ids.add(item["document_id"])
        if item["evidence_version_id"]:
            evidence_version_ids.add(item["evidence_version_id"])
    for item in plan.relations:
        document_ids.add(item["source_document_id"])
        document_ids.add(item["target_document_id"])
        if item["evidence_version_id"]:
            evidence_version_ids.add(item["evidence_version_id"])

    if document_ids:
        cur.execute(
            "SELECT document_id::text FROM documents WHERE document_id = ANY(%s::uuid[])",
            (list(document_ids),),
        )
        found = {str(row[0]) for row in cur.fetchall()}
        missing = sorted(document_ids - found)
        if missing:
            raise RegulatoryApplicabilityError(
                "document_id inexistente(s): " + ", ".join(missing)
            )

    if evidence_version_ids:
        cur.execute(
            "SELECT version_id::text FROM document_versions WHERE version_id = ANY(%s::uuid[])",
            (list(evidence_version_ids),),
        )
        found = {str(row[0]) for row in cur.fetchall()}
        missing = sorted(evidence_version_ids - found)
        if missing:
            raise RegulatoryApplicabilityError(
                "evidence_version_id inexistente(s): " + ", ".join(missing)
            )


def import_regulatory_applicability(
    plan: ImportPlan,
    *,
    execute: bool,
) -> dict[str, Any]:
    """
    Valida referências em banco e, quando execute=True, faz upsert transacional.

    O dry-run consulta o banco mas não altera dados.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            _validate_document_references(cur, plan)

            if not execute:
                return {
                    **plan.summary(),
                    "execute": False,
                    "message": "Dry-run; nenhuma alteração persistida.",
                }

            for item in plan.status_assertions:
                cur.execute(
                    """
                    INSERT INTO regulatory_status_assertions (
                      assertion_key,
                      document_id,
                      evidence_version_id,
                      status,
                      effective_from,
                      valid_to,
                      review_status,
                      source_url,
                      evidence_note,
                      asserted_by,
                      reviewed_by,
                      reviewed_at
                    )
                    VALUES (
                      %(assertion_key)s,
                      %(document_id)s::uuid,
                      %(evidence_version_id)s::uuid,
                      %(status)s,
                      %(effective_from)s::date,
                      %(valid_to)s::date,
                      %(review_status)s,
                      %(source_url)s,
                      %(evidence_note)s,
                      %(asserted_by)s,
                      %(reviewed_by)s,
                      %(reviewed_at)s::timestamptz
                    )
                    ON CONFLICT (assertion_key)
                    DO UPDATE SET
                      document_id = EXCLUDED.document_id,
                      evidence_version_id = EXCLUDED.evidence_version_id,
                      status = EXCLUDED.status,
                      effective_from = EXCLUDED.effective_from,
                      valid_to = EXCLUDED.valid_to,
                      review_status = EXCLUDED.review_status,
                      source_url = EXCLUDED.source_url,
                      evidence_note = EXCLUDED.evidence_note,
                      asserted_by = EXCLUDED.asserted_by,
                      reviewed_by = EXCLUDED.reviewed_by,
                      reviewed_at = EXCLUDED.reviewed_at,
                      updated_at = now()
                    """,
                    item,
                )

            for item in plan.relations:
                cur.execute(
                    """
                    INSERT INTO regulatory_relations (
                      relation_key,
                      source_document_id,
                      target_document_id,
                      evidence_version_id,
                      relation_type,
                      effective_date,
                      scope_note,
                      review_status,
                      source_url,
                      evidence_note,
                      asserted_by,
                      reviewed_by,
                      reviewed_at
                    )
                    VALUES (
                      %(relation_key)s,
                      %(source_document_id)s::uuid,
                      %(target_document_id)s::uuid,
                      %(evidence_version_id)s::uuid,
                      %(relation_type)s,
                      %(effective_date)s::date,
                      %(scope_note)s,
                      %(review_status)s,
                      %(source_url)s,
                      %(evidence_note)s,
                      %(asserted_by)s,
                      %(reviewed_by)s,
                      %(reviewed_at)s::timestamptz
                    )
                    ON CONFLICT (relation_key)
                    DO UPDATE SET
                      source_document_id = EXCLUDED.source_document_id,
                      target_document_id = EXCLUDED.target_document_id,
                      evidence_version_id = EXCLUDED.evidence_version_id,
                      relation_type = EXCLUDED.relation_type,
                      effective_date = EXCLUDED.effective_date,
                      scope_note = EXCLUDED.scope_note,
                      review_status = EXCLUDED.review_status,
                      source_url = EXCLUDED.source_url,
                      evidence_note = EXCLUDED.evidence_note,
                      asserted_by = EXCLUDED.asserted_by,
                      reviewed_by = EXCLUDED.reviewed_by,
                      reviewed_at = EXCLUDED.reviewed_at,
                      updated_at = now()
                    """,
                    item,
                )
        conn.commit()

    return {
        **plan.summary(),
        "execute": True,
        "message": "Lote de aplicabilidade persistido.",
    }


def empty_regulatory_context(document_id: str) -> dict[str, Any]:
    return {
        "document_id": document_id,
        "status": {
            "status": "unknown",
            "basis": NO_STATUS_BASIS,
            "assertion_id": None,
            "assertion_key": None,
            "effective_from": None,
            "valid_to": None,
            "source_url": None,
            "evidence_version_id": None,
            "evidence_note": None,
            "reviewed_by": None,
            "reviewed_at": None,
        },
        "relations": [],
    }


def load_regulatory_context(
    document_ids: Iterable[str],
    *,
    max_relations_per_document: int = 20,
) -> dict[str, dict[str, Any]]:
    ids = tuple(dict.fromkeys(str(value) for value in document_ids if value))
    if not ids:
        return {}

    contexts = {document_id: empty_regulatory_context(document_id) for document_id in ids}

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT ON (a.document_id)
                  a.document_id,
                  a.assertion_id,
                  a.assertion_key,
                  a.status,
                  a.effective_from,
                  a.valid_to,
                  a.source_url,
                  a.evidence_version_id,
                  a.evidence_note,
                  a.reviewed_by,
                  a.reviewed_at
                FROM regulatory_status_assertions a
                WHERE a.document_id = ANY(%s::uuid[])
                  AND a.review_status = 'approved'
                  AND (a.effective_from IS NULL OR a.effective_from <= CURRENT_DATE)
                  AND (a.valid_to IS NULL OR a.valid_to >= CURRENT_DATE)
                ORDER BY
                  a.document_id,
                  a.effective_from DESC NULLS LAST,
                  a.reviewed_at DESC NULLS LAST,
                  a.updated_at DESC
                """,
                (list(ids),),
            )
            for row in cur.fetchall():
                document_id = str(row[0])
                contexts[document_id]["status"] = {
                    "status": row[3],
                    "basis": APPROVED_STATUS_BASIS,
                    "assertion_id": str(row[1]),
                    "assertion_key": row[2],
                    "effective_from": row[4].isoformat() if row[4] else None,
                    "valid_to": row[5].isoformat() if row[5] else None,
                    "source_url": row[6],
                    "evidence_version_id": str(row[7]) if row[7] else None,
                    "evidence_note": row[8],
                    "reviewed_by": row[9],
                    "reviewed_at": row[10].isoformat() if row[10] else None,
                }

            cur.execute(
                """
                SELECT
                  r.relation_id,
                  r.relation_key,
                  r.source_document_id,
                  src.title,
                  r.target_document_id,
                  tgt.title,
                  r.relation_type,
                  r.effective_date,
                  r.scope_note,
                  r.source_url,
                  r.evidence_version_id,
                  r.evidence_note,
                  r.reviewed_by,
                  r.reviewed_at
                FROM regulatory_relations r
                JOIN documents src ON src.document_id = r.source_document_id
                JOIN documents tgt ON tgt.document_id = r.target_document_id
                WHERE r.review_status = 'approved'
                  AND (
                    r.source_document_id = ANY(%s::uuid[])
                    OR r.target_document_id = ANY(%s::uuid[])
                  )
                  AND (r.effective_date IS NULL OR r.effective_date <= CURRENT_DATE)
                ORDER BY
                  r.effective_date DESC NULLS LAST,
                  r.reviewed_at DESC NULLS LAST,
                  r.relation_id
                """,
                (list(ids), list(ids)),
            )
            per_document_counts = {document_id: 0 for document_id in ids}
            for row in cur.fetchall():
                relation_id = str(row[0])
                source_id = str(row[2])
                target_id = str(row[4])
                common = {
                    "relation_id": relation_id,
                    "relation_key": row[1],
                    "relation_type": row[6],
                    "effective_date": row[7].isoformat() if row[7] else None,
                    "scope_note": row[8],
                    "source_url": row[9],
                    "evidence_version_id": str(row[10]) if row[10] else None,
                    "evidence_note": row[11],
                    "reviewed_by": row[12],
                    "reviewed_at": row[13].isoformat() if row[13] else None,
                    "basis": "approved_curated_relation",
                }
                if source_id in contexts and per_document_counts[source_id] < max_relations_per_document:
                    contexts[source_id]["relations"].append(
                        {
                            **common,
                            "direction": "outbound",
                            "related_document_id": target_id,
                            "related_document_title": row[5],
                        }
                    )
                    per_document_counts[source_id] += 1
                if target_id in contexts and per_document_counts[target_id] < max_relations_per_document:
                    contexts[target_id]["relations"].append(
                        {
                            **common,
                            "direction": "inbound",
                            "related_document_id": source_id,
                            "related_document_title": row[3],
                        }
                    )
                    per_document_counts[target_id] += 1

    return contexts


def attach_regulatory_context(
    rows: list[dict[str, Any]],
    *,
    enabled: bool,
    max_relations_per_document: int = 20,
) -> list[dict[str, Any]]:
    """
    Acrescenta metadados curados sem alterar ranking, score ou seleção.
    """
    if not enabled or not rows:
        return rows

    document_ids = [
        str(row.get("document", {}).get("document_id") or "")
        for row in rows
    ]
    contexts = load_regulatory_context(
        document_ids,
        max_relations_per_document=max_relations_per_document,
    )

    enriched: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        document = dict(item.get("document") or {})
        document_id = str(document.get("document_id") or "")
        document["regulatory_context"] = contexts.get(
            document_id,
            empty_regulatory_context(document_id),
        )
        item["document"] = document
        enriched.append(item)
    return enriched
