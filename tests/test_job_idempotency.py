from __future__ import annotations

from contextlib import contextmanager

from intelireg.jobs import (
    enqueue_index_version_job,
    index_version_idempotency_key,
)
from intelireg.cli.enqueue_reindex_all import _reindex_plan


class FakeCursor:
    def __init__(self, fetches):
        self.fetches = list(fetches)
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, tuple(params or ())))

    def fetchone(self):
        if not self.fetches:
            raise AssertionError("fetchone inesperado")
        return self.fetches.pop(0)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeConn:
    def __init__(self, fetches):
        self.cursor_obj = FakeCursor(fetches)
        self.commits = 0

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.commits += 1


def _conn_factory(*fetch_sequences):
    connections = [FakeConn(fetches) for fetches in fetch_sequences]

    @contextmanager
    def fake_get_conn():
        if not connections:
            raise AssertionError("get_conn inesperado")
        yield connections.pop(0)

    return fake_get_conn


def test_index_version_idempotency_key_is_stable():
    assert (
        index_version_idempotency_key(
            "11111111-1111-1111-1111-111111111111",
            "mvp-v2-semantic-v1",
        )
        == "IndexVersionJob:mvp-v2-semantic-v1:"
        "11111111-1111-1111-1111-111111111111"
    )


def test_enqueue_index_version_job_creates_first_job(monkeypatch):
    monkeypatch.setattr(
        "intelireg.jobs.get_conn",
        _conn_factory([(321,)]),
    )
    notifications = []
    monkeypatch.setattr(
        "intelireg.jobs._notify_new_job",
        lambda: notifications.append(True),
    )

    result = enqueue_index_version_job(
        {
            "version_id": "11111111-1111-1111-1111-111111111111",
            "pipeline_version": "semantic-v1",
            "embedding_model_id": "model@384",
            "force": True,
        }
    )

    assert result.job_id == 321
    assert result.created is True
    assert notifications == [True]


def test_enqueue_index_version_job_reuses_existing_active_job(monkeypatch):
    monkeypatch.setattr(
        "intelireg.jobs.get_conn",
        _conn_factory([None, (321,)]),
    )
    notifications = []
    monkeypatch.setattr(
        "intelireg.jobs._notify_new_job",
        lambda: notifications.append(True),
    )

    result = enqueue_index_version_job(
        {
            "version_id": "11111111-1111-1111-1111-111111111111",
            "pipeline_version": "semantic-v1",
            "embedding_model_id": "model@384",
            "force": True,
        }
    )

    assert result.job_id == 321
    assert result.created is False
    assert notifications == []


def test_reindex_plan_reports_missing_active_and_eligible(monkeypatch):
    monkeypatch.setattr(
        "intelireg.cli.enqueue_reindex_all.get_conn",
        _conn_factory([(10, 4, 6)]),
    )

    plan = _reindex_plan("semantic-v1", limit=3)

    assert plan == {
        "missing_pipeline": 10,
        "already_active": 4,
        "eligible_to_enqueue": 6,
        "selected_to_enqueue": 3,
    }
