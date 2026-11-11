from contextlib import contextmanager

import pytest

from intelireg import db, settings


def test_connection_sets_quoted_schema_before_yield_and_closes(monkeypatch):
    calls = []

    class Connection:
        @contextmanager
        def cursor(self):
            yield self

        def execute(self, statement):
            calls.append(statement.as_string())

        def commit(self):
            calls.append("commit")

        def close(self):
            calls.append("close")

    connection = Connection()
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused/test")
    monkeypatch.setattr(settings, "PG_SCHEMA", 'odd"schema')
    monkeypatch.setattr(db.psycopg, "connect", lambda *a, **kw: connection)
    with pytest.raises(RuntimeError, match="caller failed"):
        with db.get_conn() as actual:
            assert actual is connection
            assert calls == ['SET search_path TO "odd""schema", public', "commit"]
            raise RuntimeError("caller failed")
    assert calls[-1] == "close"


def test_connection_closes_when_schema_configuration_fails(monkeypatch):
    class Connection:
        closed = False

        @contextmanager
        def cursor(self):
            raise RuntimeError("schema setup failed")
            yield

        def close(self):
            self.closed = True

    connection = Connection()
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused/test")
    monkeypatch.setattr(db.psycopg, "connect", lambda *a, **kw: connection)
    with pytest.raises(RuntimeError, match="schema setup failed"):
        with db.get_conn():
            pytest.fail("must not yield an unconfigured connection")
    assert connection.closed
