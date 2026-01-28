from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional, Dict

from .db import get_conn

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
                (job_type, json.dumps(payload)),
            )
            job_id = cur.fetchone()[0]
        conn.commit()

    # NOTIFY é “best-effort”: acorda worker, mas não é garantia (a tabela é a fonte da verdade)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("NOTIFY jobs_channel, 'new_job';")
        conn.commit()

    return job_id

def fetch_next_job(worker_id: str) -> Optional[Job]:
    """
    Busca 1 job elegível e faz lock cooperativo (SKIP LOCKED).
    Retorna None se não houver job.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT job_id, type, payload
                FROM jobs
                WHERE status IN ('queued','failed')
                  AND run_after <= now()
                ORDER BY run_after ASC, job_id ASC
                FOR UPDATE SKIP LOCKED
                LIMIT 1
                """
            )
            row = cur.fetchone()
            if not row:
                conn.commit()
                return None

            job_id, job_type, payload = row

            cur.execute(
                """
                UPDATE jobs
                SET status='running',
                    locked_at=now(),
                    locked_by=%s,
                    updated_at=now()
                WHERE job_id=%s
                """,
                (worker_id, job_id),
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
                    last_error=%s,
                    run_after = now() + (%s || ' seconds')::interval,
                    updated_at=now()
                WHERE job_id=%s
                """,
                (error, str(backoff_seconds), job_id),
            )
        conn.commit()
