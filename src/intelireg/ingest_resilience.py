from __future__ import annotations

import json
import math
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Callable, Optional

import httpx


RETRYABLE_HTTP_STATUSES = frozenset({408, 425, 429, *range(500, 600)})


@dataclass(frozen=True)
class FetchResult:
    html: str
    final_url: str
    http_status: int
    attempts: int


class FetchFailure(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        url: str,
        attempts: int,
        max_attempts: int,
        retryable: bool,
        http_status: Optional[int] = None,
        final_url: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.url = url
        self.attempts = attempts
        self.max_attempts = max_attempts
        self.retryable = retryable
        self.http_status = http_status
        self.final_url = final_url


@dataclass(frozen=True)
class IngestFailureRecord:
    timestamp: str
    event: str
    url: str
    stage: str
    error_type: str
    message: str
    http_status: Optional[int] = None
    final_url: Optional[str] = None
    attempts: Optional[int] = None
    max_attempts: Optional[int] = None
    retryable: Optional[bool] = None


def _retry_after_seconds(response: httpx.Response) -> Optional[float]:
    raw = response.headers.get("Retry-After")
    if not raw:
        return None

    try:
        seconds = float(raw)
        return max(0.0, seconds) if math.isfinite(seconds) else None
    except ValueError:
        pass

    try:
        retry_at = parsedate_to_datetime(raw)
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return None


def _retry_delay(
    response: Optional[httpx.Response],
    attempt: int,
    backoff_seconds: float,
    max_backoff_seconds: float,
) -> float:
    retry_after = _retry_after_seconds(response) if response is not None else None
    if retry_after is not None:
        return min(retry_after, max_backoff_seconds)
    return min(backoff_seconds * (2 ** (attempt - 1)), max_backoff_seconds)


def fetch_html_with_retries(
    client: httpx.Client,
    url: str,
    *,
    max_attempts: int = 3,
    backoff_seconds: float = 2.0,
    max_backoff_seconds: float = 60.0,
    sleep: Callable[[float], None] = time.sleep,
    on_retry: Optional[Callable[[int, float, str], None]] = None,
) -> FetchResult:
    """Busca HTML, repetindo apenas falhas que têm chance de ser transitórias."""

    if max_attempts < 1:
        raise ValueError("max_attempts deve ser maior ou igual a 1")
    if (
        backoff_seconds < 0
        or max_backoff_seconds < 0
        or not math.isfinite(backoff_seconds)
        or not math.isfinite(max_backoff_seconds)
    ):
        raise ValueError("os intervalos de backoff não podem ser negativos")

    for attempt in range(1, max_attempts + 1):
        response: Optional[httpx.Response] = None
        try:
            response = client.get(url)
        except httpx.RequestError as exc:
            if attempt >= max_attempts:
                raise FetchFailure(
                    f"falha de rede após {attempt} tentativa(s): {exc}",
                    url=url,
                    attempts=attempt,
                    max_attempts=max_attempts,
                    retryable=True,
                ) from exc

            delay = _retry_delay(
                None, attempt, backoff_seconds, max_backoff_seconds
            )
            if on_retry:
                on_retry(attempt, delay, type(exc).__name__)
            sleep(delay)
            continue

        status = int(response.status_code)
        final_url = str(response.url)
        if 200 <= status < 300:
            if not response.text.strip():
                raise FetchFailure(
                    "a resposta HTTP não contém conteúdo",
                    url=url,
                    attempts=attempt,
                    max_attempts=max_attempts,
                    retryable=False,
                    http_status=status,
                    final_url=final_url,
                )
            return FetchResult(
                html=response.text,
                final_url=final_url,
                http_status=status,
                attempts=attempt,
            )

        retryable = status in RETRYABLE_HTTP_STATUSES
        if retryable and attempt < max_attempts:
            delay = _retry_delay(
                response, attempt, backoff_seconds, max_backoff_seconds
            )
            if on_retry:
                on_retry(attempt, delay, f"HTTP {status}")
            sleep(delay)
            continue

        qualifier = "após esgotar as tentativas" if retryable else "erro permanente"
        raise FetchFailure(
            f"HTTP {status} ao buscar a URL ({qualifier})",
            url=url,
            attempts=attempt,
            max_attempts=max_attempts,
            retryable=retryable,
            http_status=status,
            final_url=final_url,
        )

    raise AssertionError("fluxo de tentativas terminou inesperadamente")


def failure_record(url: str, stage: str, exc: Exception) -> IngestFailureRecord:
    if isinstance(exc, FetchFailure):
        return IngestFailureRecord(
            timestamp=datetime.now(timezone.utc).isoformat(),
            event="ingest_failure",
            url=url,
            stage=stage,
            error_type=type(exc).__name__,
            message=str(exc)[:2000],
            http_status=exc.http_status,
            final_url=exc.final_url,
            attempts=exc.attempts,
            max_attempts=exc.max_attempts,
            retryable=exc.retryable,
        )

    return IngestFailureRecord(
        timestamp=datetime.now(timezone.utc).isoformat(),
        event="ingest_failure",
        url=url,
        stage=stage,
        error_type=type(exc).__name__,
        message=str(exc)[:2000],
    )


def append_failure_record(path: str, record: IngestFailureRecord) -> None:
    """Acrescenta um evento JSONL sem apagar registros de outras URLs."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(asdict(record), ensure_ascii=False, separators=(",", ":"))
    data = (payload + "\n").encode("utf-8")
    fd = os.open(target, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o640)
    try:
        remaining = memoryview(data)
        while remaining:
            written = os.write(fd, remaining)
            remaining = remaining[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
