from __future__ import annotations

import json
import sys
from uuid import uuid4

from intelireg import settings
from intelireg.db import get_conn
from intelireg.jobs import index_version_idempotency_key


def main() -> int:
    """
    Smoke test transacional da restrição de idempotência.

    Insere duas vezes a mesma chave dentro de uma transação e SEMPRE executa
    rollback ao final. Não deixa jobs de teste no banco e não depende do worker.
    """
    probe_pipeline = f"idempotency-probe-{uuid4()}"
    probe_version = str(uuid4())
    key = index_version_idempotency_key(probe_version, probe_pipeline)

    with get_conn() as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO jobs (
                      type,
                      payload,
                      status,
                      idempotency_key,
                      run_after
                    )
                    VALUES (
                      'IndexVersionJob',
                      jsonb_build_object(
                        'version_id', %s::text,
                        'pipeline_version', %s::text,
                        'embedding_model_id', %s::text,
                        'force', true
                      ),
                      'queued',
                      %s,
                      now() + interval '1 day'
                    )
                    ON CONFLICT (type, idempotency_key)
                      WHERE idempotency_key IS NOT NULL
                        AND status IN ('queued', 'running', 'failed')
                    DO NOTHING
                    RETURNING job_id
                    """,
                    (
                        probe_version,
                        probe_pipeline,
                        settings.EMBEDDING_MODEL_ID,
                        key,
                    ),
                )
                first = cur.fetchone()

                cur.execute(
                    """
                    INSERT INTO jobs (
                      type,
                      payload,
                      status,
                      idempotency_key,
                      run_after
                    )
                    VALUES (
                      'IndexVersionJob',
                      jsonb_build_object(
                        'version_id', %s::text,
                        'pipeline_version', %s::text,
                        'embedding_model_id', %s::text,
                        'force', true
                      ),
                      'queued',
                      %s,
                      now() + interval '1 day'
                    )
                    ON CONFLICT (type, idempotency_key)
                      WHERE idempotency_key IS NOT NULL
                        AND status IN ('queued', 'running', 'failed')
                    DO NOTHING
                    RETURNING job_id
                    """,
                    (
                        probe_version,
                        probe_pipeline,
                        settings.EMBEDDING_MODEL_ID,
                        key,
                    ),
                )
                second = cur.fetchone()

                cur.execute(
                    """
                    SELECT COUNT(*)::bigint
                    FROM jobs
                    WHERE type = 'IndexVersionJob'
                      AND idempotency_key = %s
                      AND status IN ('queued', 'running', 'failed')
                    """,
                    (key,),
                )
                active_count = int(cur.fetchone()[0] or 0)
        finally:
            conn.rollback()

    result = {
        "status": (
            "PASS"
            if first is not None
            and second is None
            and active_count == 1
            else "FAIL"
        ),
        "first_insert_created": first is not None,
        "second_insert_created": second is not None,
        "active_count_inside_transaction": active_count,
        "rollback": True,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
