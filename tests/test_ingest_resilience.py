from __future__ import annotations

import json

import httpx
import pytest

from intelireg.cli import ingest_web
from intelireg.ingest_resilience import (
    FetchFailure,
    FetchResult,
    append_failure_record,
    failure_record,
    fetch_html_with_retries,
)


def test_fetch_returns_success_without_retry() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, text="<html>ok</html>", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = fetch_html_with_retries(client, "https://example.test/doc")

    assert result.http_status == 200
    assert result.attempts == 1
    assert result.html == "<html>ok</html>"
    assert calls == 1


def test_fetch_does_not_retry_404() -> None:
    calls = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(404, text="not found", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(FetchFailure) as raised:
            fetch_html_with_retries(
                client,
                "https://example.test/broken",
                max_attempts=3,
                sleep=sleeps.append,
            )

    assert calls == 1
    assert sleeps == []
    assert raised.value.http_status == 404
    assert raised.value.attempts == 1
    assert raised.value.retryable is False


def test_fetch_retries_429_and_honors_capped_retry_after() -> None:
    calls = 0
    sleeps: list[float] = []
    retries: list[tuple[int, float, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                429,
                headers={"Retry-After": "30"},
                text="slow down",
                request=request,
            )
        return httpx.Response(200, text="<html>ok</html>", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = fetch_html_with_retries(
            client,
            "https://example.test/rate-limited",
            max_attempts=3,
            max_backoff_seconds=5,
            sleep=sleeps.append,
            on_retry=lambda attempt, delay, reason: retries.append(
                (attempt, delay, reason)
            ),
        )

    assert result.attempts == 2
    assert calls == 2
    assert sleeps == [5]
    assert retries == [(1, 5, "HTTP 429")]


def test_fetch_retries_network_error_until_exhausted() -> None:
    calls = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("timed out", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(FetchFailure) as raised:
            fetch_html_with_retries(
                client,
                "https://example.test/timeout",
                max_attempts=3,
                backoff_seconds=2,
                sleep=sleeps.append,
            )

    assert calls == 3
    assert sleeps == [2, 4]
    assert raised.value.attempts == 3
    assert raised.value.retryable is True


def test_failure_log_is_append_only_jsonl(tmp_path) -> None:
    path = tmp_path / "logs" / "failures.jsonl"
    error = FetchFailure(
        "HTTP 404 ao buscar a URL (erro permanente)",
        url="https://example.test/missing",
        attempts=1,
        max_attempts=3,
        retryable=False,
        http_status=404,
        final_url="https://example.test/missing",
    )
    record = failure_record("https://example.test/missing", "fetch", error)

    append_failure_record(str(path), record)
    append_failure_record(str(path), record)

    events = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(events) == 2
    assert events[0]["stage"] == "fetch"
    assert events[0]["http_status"] == 404
    assert events[0]["attempts"] == 1
    assert events[0]["retryable"] is False


def test_cli_records_the_stage_where_ingest_broke(tmp_path, monkeypatch) -> None:
    path = tmp_path / "ingest_failures.jsonl"

    monkeypatch.setattr(
        ingest_web,
        "fetch_html_with_retries",
        lambda *args, **kwargs: FetchResult(
            html="<html>conteúdo</html>",
            final_url="https://example.test/doc",
            http_status=200,
            attempts=1,
        ),
    )
    monkeypatch.setattr(
        ingest_web,
        "extract_nodes_auto",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("HTML inesperado")
        ),
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "ingest_web",
            "--url",
            "https://example.test/doc",
            "--source-org",
            "ANVISA",
            "--doc-type",
            "rdc",
            "--failure-log",
            str(path),
        ],
    )

    assert ingest_web.main() == 1

    event = json.loads(path.read_text())
    assert event["url"] == "https://example.test/doc"
    assert event["stage"] == "extract"
    assert event["error_type"] == "RuntimeError"
    assert event["message"] == "HTML inesperado"
