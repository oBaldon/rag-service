from __future__ import annotations

import json
from uuid import UUID
from typing import Any, Dict, Optional
from dataclasses import dataclass
from datetime import date, datetime

from .db import get_conn

DEFAULT_LEASE_SECONDS = 15 * 60  # 15 min (ajuste depois se quiser)
ACTIVE_JOB_STATUSES = ("queued", "running", "failed")


def _json_default(o: Any) -> Any:
    """
    Permite serializar payload com UUID/datetime etc.
    """
    if isinstance(o, UUID):
        return str(o)
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    return str(o)


@dataclass
class Job:
    job_id: int
    type: str
    payload: Dict[str, Any]


@dataclass(frozen=True)
class EnqueueResult:
    job_id: int
    created: bool


def index_version_idempotency_key(
    version_id: str,
    pipeline_version: str,
) -> str:
    version = str(version_id or "").strip()
    pipeline = str(pipeline_version or "").strip()
    if not version:
        raise ValueError("version_id não pode ser vazio")
    if not pipeline:
        raise ValueError("pipeline_version não pode ser vazio")
    return f"IndexVersionJob:{pipeline}:{version}"


def _notify_new_job() -> None:
    # NOTIFY é best-effort: acorda worker, mas a tabela é a fonte da verdade.
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("NOTIFY jobs_channel, 'new_job';")
        conn.commit()


def enqueue_job(
    job_type: str,
    payload: Dict[str, Any],
    *,
    idempotency_key: str | None = None,
) -> int:
    """
    Enfileiramento genérico.

    Se idempotency_key for informado, a migration 0004 garante no banco que
    só exista um job ATIVO (queued/running/failed) por type + chave.
    Nesse caso, uma chamada concorrente reutiliza o job ativo existente.
    """
    payload_json = json.dumps(
        payload,
        default=_json_default,
        ensure_ascii=False,
    )

    if not idempotency_key:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO jobs (type, payload, status)
                    VALUES (%s, %s::jsonb, 'queued')
                    RETURNING job_id
                    """,
                    (job_type, payload_json),
                )
                job_id = int(cur.fetchone()[0])
            conn.commit()
        _notify_new_job()
        return job_id

    key = str(idempotency_key).strip()
    if not key:
        raise ValueError("idempotency_key não pode ser vazio")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO jobs (
                  type,
                  payload,
                  status,
                  idempotency_key
                )
                VALUES (%s, %s::jsonb, 'queued', %s)
                ON CONFLICT (type, idempotency_key)
                  WHERE idempotency_key IS NOT NULL
                    AND status IN ('queued', 'running', 'failed')
                DO NOTHING
                RETURNING job_id
                """,
                (job_type, payload_json, key),
            )
            row = cur.fetchone()
            created = row is not None
            if row is not None:
                job_id = int(row[0])
            else:
                cur.execute(
                    """
                    SELECT job_id
                    FROM jobs
                    WHERE type = %s
                      AND idempotency_key = %s
                      AND status IN ('queued', 'running', 'failed')
                    ORDER BY job_id ASC
                    LIMIT 1
                    """,
                    (job_type, key),
                )
                existing = cur.fetchone()
                if existing is None:
                    raise RuntimeError(
                        "Conflito de idempotência ocorreu, mas o job ativo "
                        "não pôde ser localizado."
                    )
                job_id = int(existing[0])
        conn.commit()

    if created:
        _notify_new_job()
    return job_id


def enqueue_index_version_job(payload: Dict[str, Any]) -> EnqueueResult:
    version_id = str(payload.get("version_id") or "").strip()
    pipeline_version = str(payload.get("pipeline_version") or "").strip()
    key = index_version_idempotency_key(version_id, pipeline_version)

    payload_json = json.dumps(
        payload,
        default=_json_default,
        ensure_ascii=False,
    )

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO jobs (
                  type,
                  payload,
                  status,
                  idempotency_key
                )
                VALUES (
                  'IndexVersionJob',
                  %s::jsonb,
                  'queued',
                  %s
                )
                ON CONFLICT (type, idempotency_key)
                  WHERE idempotency_key IS NOT NULL
                    AND status IN ('queued', 'running', 'failed')
                DO NOTHING
                RETURNING job_id
                """,
                (payload_json, key),
            )
            row = cur.fetchone()
            created = row is not None
            if row is not None:
                job_id = int(row[0])
            else:
                cur.execute(
                    """
                    SELECT job_id
                    FROM jobs
                    WHERE type = 'IndexVersionJob'
                      AND idempotency_key = %s
                      AND status IN ('queued', 'running', 'failed')
                    ORDER BY job_id ASC
                    LIMIT 1
                    """,
                    (key,),
                )
                existing = cur.fetchone()
                if existing is None:
                    raise RuntimeError(
                        "IndexVersionJob idempotente não foi criado nem "
                        "localizado entre jobs ativos."
                    )
                job_id = int(existing[0])
        conn.commit()

    if created:
        _notify_new_job()
    return EnqueueResult(job_id=job_id, created=created)


def fetch_next_job(
    worker_id: str,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> Optional[Job]:
    """
    Busca 1 job elegível e faz lock cooperativo (SKIP LOCKED).
    Retorna None se não houver job.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT job_id, type, payload, status, locked_at
                FROM jobs
                WHERE (
                  (status IN ('queued','failed') AND run_after <= now())
                  OR (
                    status = 'running'
                    AND locked_at IS NOT NULL
                    AND locked_at <= now() - (%s || ' seconds')::interval
                  )
                )
                ORDER BY run_after ASC, job_id ASC
                FOR UPDATE SKIP LOCKED
                LIMIT 1
                """,
                (str(lease_seconds),),
            )
            row = cur.fetchone()
            if not row:
                conn.commit()
                return None

            job_id, job_type, payload, prev_status, prev_locked_at = row

            cur.execute(
                """
                UPDATE jobs
                SET status='running',
                    locked_at=now(),
                    locked_by=%s,
                    -- se estava "running" e expirou lease, apenas anotamos o reclaim;
                    -- attempts deve refletir falhas (incrementa em mark_failed), evitando dobrar.
                    last_error = CASE
                      WHEN %s = 'running'
                        THEN concat_ws(E'\n', NULLIF(last_error,''), 'reclaimed: lease expired')
                      ELSE last_error
                    END,
                    updated_at=now()
                WHERE job_id=%s
                """,
                (worker_id, prev_status, job_id),
            )
        conn.commit()

    return Job(job_id=job_id, type=job_type, payload=payload)


def mark_done(job_id: int) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE jobs
                SET status='done',
                    locked_at=NULL,
                    locked_by=NULL,
                    updated_at=now()
                WHERE job_id=%s
                """,
                (job_id,),
            )
        conn.commit()


def mark_failed(
    job_id: int,
    error: str,
    backoff_seconds: int = 10,
) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE jobs
                SET status='failed',
                    attempts = attempts + 1,
                    last_error = concat_ws(E'\n', NULLIF(last_error,''), %s::text),
                    run_after = now() + make_interval(secs => %s),
                    locked_at=NULL,
                    locked_by=NULL,
                    updated_at=now()
                WHERE job_id=%s
                """,
                (error, int(backoff_seconds), job_id),
            )
        conn.commit()
