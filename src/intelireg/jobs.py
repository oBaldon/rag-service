from __future__ import annotations

import json
from uuid import UUID
from typing import Any, Dict, Optional
from dataclasses import dataclass
from datetime import date, datetime


from .db import get_conn

DEFAULT_LEASE_SECONDS = 15 * 60  # 15 min (ajuste depois se quiser)

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

def enqueue_job(job_type: str, payload: Dict[str, Any]) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO jobs (type, payload, status)
                VALUES (%s, %s::jsonb, 'queued')
                RETURNING job_id
                """,
                (job_type, json.dumps(payload, default=_json_default, ensure_ascii=False)),
            )
            job_id = cur.fetchone()[0]
        conn.commit()

    # NOTIFY é “best-effort”: acorda worker, mas não é garantia (a tabela é a fonte da verdade)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("NOTIFY jobs_channel, 'new_job';")
        conn.commit()

    return job_id

def fetch_next_job(worker_id: str, lease_seconds: int = DEFAULT_LEASE_SECONDS) -> Optional[Job]:
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

def mark_failed(job_id: int, error: str, backoff_seconds: int = 10) -> None:
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
