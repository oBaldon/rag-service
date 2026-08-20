from __future__ import annotations

from intelireg import settings
from intelireg.retrieval import (
    _find_exact_document_versions,
    build_retrieval_plan,
    lexical_coverage,
    rerank_candidates,
)


def _candidate(
    *,
    chunk_id: str,
    title: str,
    text: str,
    rrf: float,
    document_id: str,
    exact: bool = False,
):
    return {
        "chunk_id": chunk_id,
        "rrf_score": rrf,
        "fts_rank": None,
        "fts_score": None,
        "vec_rank": None,
        "vec_distance": None,
        "version_id": "00000000-0000-0000-0000-000000000001",
        "pipeline_version": "mvp-v1",
        "chunk_index": 0,
        "tokens_count": 10,
        "text": text,
        "node_refs": [],
        "document": {
            "document_id": document_id,
            "title": title,
            "source_org": "ANVISA",
            "doc_type": "rdc",
            "source_url": "https://example.test",
            "final_url": None,
            "captured_at": None,
        },
        "exact_identifier_match": exact,
        "exact_identifier_rank": 1 if exact else None,
    }


def test_lexical_coverage_handles_simple_portuguese_plural():
    coverage = lexical_coverage(
        "importação excepcional e temporária de medicamento ou vacina",
        "RDC 476",
        "A importação excepcional e temporária de medicamentos e vacinas.",
    )

    assert coverage == 1.0


def test_exact_identifier_candidate_wins_without_overwriting_rrf(monkeypatch):
    monkeypatch.setattr(settings, "RERANK_ENABLED", True)
    monkeypatch.setattr(settings, "RERANK_DIVERSITY_ENABLED", False)
    monkeypatch.setattr(settings, "RERANK_LEXICAL_WEIGHT", 0.012)
    monkeypatch.setattr(settings, "RERANK_EXACT_IDENTIFIER_WEIGHT", 1.0)

    wrong = _candidate(
        chunk_id="wrong",
        title="RDC 999",
        text="Texto parecido",
        rrf=0.04,
        document_id="doc-wrong",
    )
    exact = _candidate(
        chunk_id="exact",
        title="RDC 476",
        text="Preâmbulo",
        rrf=0.0,
        document_id="doc-exact",
        exact=True,
    )

    ranked = rerank_candidates("RDC nº 476, de 10/03/2021", [wrong, exact], top_k=2)

    assert ranked[0]["chunk_id"] == "exact"
    assert ranked[0]["rrf_score"] == 0.0
    assert ranked[0]["final_score"] > ranked[1]["final_score"]


def test_lexical_reranker_can_promote_more_specific_chunk(monkeypatch):
    monkeypatch.setattr(settings, "RERANK_ENABLED", True)
    monkeypatch.setattr(settings, "RERANK_DIVERSITY_ENABLED", False)
    monkeypatch.setattr(settings, "RERANK_LEXICAL_WEIGHT", 0.012)

    broad = _candidate(
        chunk_id="broad",
        title="RDC 465",
        text="Dispensa de registro e procedimentos para importação e monitoramento de vacinas.",
        rrf=0.0237,
        document_id="doc-465",
    )
    specific = _candidate(
        chunk_id="specific",
        title="RDC 476",
        text="A importação excepcional e temporária de medicamentos e vacinas será submetida à apreciação.",
        rrf=0.0154,
        document_id="doc-476",
    )

    ranked = rerank_candidates(
        "importação excepcional e temporária de medicamento ou vacina",
        [broad, specific],
        top_k=2,
    )

    assert ranked[0]["chunk_id"] == "specific"
    assert ranked[0]["lexical_coverage"] > ranked[1]["lexical_coverage"]


def test_retrieval_plan_expands_both_enabled_channels(monkeypatch):
    monkeypatch.setattr(settings, "RERANK_ENABLED", True)
    monkeypatch.setattr(settings, "RERANK_CANDIDATE_MULTIPLIER", 4)
    monkeypatch.setattr(settings, "RERANK_CANDIDATES_MAX", 80)
    monkeypatch.setattr(settings, "RETRIEVAL_CANDIDATES_MAX", 200)

    plan = build_retrieval_plan(top_k=12, n1_fts=30, n2_vec=30)

    assert plan == {
        "candidate_limit": 48,
        "effective_n1_fts": 48,
        "effective_n2_vec": 48,
    }


def test_retrieval_plan_does_not_enable_disabled_channel(monkeypatch):
    monkeypatch.setattr(settings, "RERANK_ENABLED", True)
    monkeypatch.setattr(settings, "RERANK_CANDIDATE_MULTIPLIER", 4)
    monkeypatch.setattr(settings, "RERANK_CANDIDATES_MAX", 80)
    monkeypatch.setattr(settings, "RETRIEVAL_CANDIDATES_MAX", 200)

    plan = build_retrieval_plan(top_k=12, n1_fts=30, n2_vec=0)

    assert plan["effective_n1_fts"] == 48
    assert plan["effective_n2_vec"] == 0


class _ExactLookupCursor:
    def __init__(self, rows):
        self._rows = rows
        self.sql = ""
        self.params = []

    def execute(self, sql, params):
        self.sql = sql
        self.params = list(params)
        assert sql.count("%s") == len(self.params)

    def fetchall(self):
        return self._rows


def test_exact_lookup_filters_same_number_wrong_normative_family():
    from datetime import datetime, timezone
    from uuid import uuid4
    from intelireg.regulatory_identifiers import parse_regulatory_identifier

    query = parse_regulatory_identifier("RDC nº 476, de 10/03/2021")
    assert query is not None

    cursor = _ExactLookupCursor(
        [
            (
                uuid4(),
                "Resolução da Diretoria Colegiada - RDC no 476, de 10/03/2021",
                "rdc",
                uuid4(),
                datetime.now(timezone.utc),
            ),
            (
                uuid4(),
                "Portaria - PRT no 476, de 10/03/2021",
                "prt",
                uuid4(),
                datetime.now(timezone.utc),
            ),
        ]
    )

    matched = _find_exact_document_versions(
        cursor,
        query,
        pipeline_version="mvp-v1",
        version_id=None,
    )

    assert len(matched) == 1
    assert "RDC no 476" in matched[0]["title"]
