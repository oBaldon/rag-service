from __future__ import annotations

import argparse
import json
from typing import Any

from intelireg import settings
from intelireg.db import get_conn
from intelireg.semantic_vocabulary import vocabulary_summary


_ACTIVE_JOB_STATUSES_SQL = "('queued', 'running', 'failed')"


def _reindex_plan(
    pipeline_version: str,
    limit: int | None,
) -> dict[str, int]:
    """
    Resume o estado antes de qualquer escrita.

    missing_pipeline:
        versões INDEXED ainda sem chunks no pipeline de destino.

    already_active:
        subconjunto de missing_pipeline que já possui IndexVersionJob ativo
        para o mesmo version_id + pipeline_version.

    eligible_to_enqueue:
        missing_pipeline sem job ativo.

    selected_to_enqueue:
        quantidade que seria efetivamente escolhida após --limit.
    """
    sql = f"""
        WITH missing AS (
          SELECT v.version_id
          FROM document_versions v
          WHERE v.status = 'INDEXED'
            AND NOT EXISTS (
              SELECT 1
              FROM embedding_chunks c
              WHERE c.version_id = v.version_id
                AND c.pipeline_version = %s
            )
        ),
        classified AS (
          SELECT
            m.version_id,
            EXISTS (
              SELECT 1
              FROM jobs j
              WHERE j.type = 'IndexVersionJob'
                AND j.status IN {_ACTIVE_JOB_STATUSES_SQL}
                AND j.payload->>'version_id' = m.version_id::text
                AND j.payload->>'pipeline_version' = %s
            ) AS already_active
          FROM missing m
        )
        SELECT
          COUNT(*)::bigint AS missing_pipeline,
          COUNT(*) FILTER (WHERE already_active)::bigint AS already_active,
          COUNT(*) FILTER (WHERE NOT already_active)::bigint AS eligible_to_enqueue
        FROM classified
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (pipeline_version, pipeline_version))
            row = cur.fetchone()

    missing_pipeline = int(row[0] or 0)
    already_active = int(row[1] or 0)
    eligible_to_enqueue = int(row[2] or 0)
    selected_to_enqueue = (
        min(eligible_to_enqueue, int(limit))
        if limit is not None
        else eligible_to_enqueue
    )
    return {
        "missing_pipeline": missing_pipeline,
        "already_active": already_active,
        "eligible_to_enqueue": eligible_to_enqueue,
        "selected_to_enqueue": selected_to_enqueue,
    }


def _active_duplicate_groups(
    pipeline_version: str,
) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT COUNT(*)::bigint
                FROM (
                  SELECT
                    payload->>'version_id' AS version_id,
                    payload->>'pipeline_version' AS pipeline_version
                  FROM jobs
                  WHERE type = 'IndexVersionJob'
                    AND status IN {_ACTIVE_JOB_STATUSES_SQL}
                    AND payload->>'pipeline_version' = %s
                    AND NULLIF(payload->>'version_id', '') IS NOT NULL
                  GROUP BY
                    payload->>'version_id',
                    payload->>'pipeline_version'
                  HAVING COUNT(*) > 1
                ) duplicates
                """,
                (pipeline_version,),
            )
            return int(cur.fetchone()[0] or 0)


def _enqueue_missing_versions(
    *,
    pipeline_version: str,
    embedding_model_id: str,
    limit: int | None,
) -> dict[str, int | None]:
    """
    Seleção + INSERT acontecem no mesmo statement/transação.

    A migration 0004 adiciona uma unique partial index em jobs(type,
    idempotency_key) para estados ativos. Mesmo que dois processos executem
    este comando ao mesmo tempo, o PostgreSQL deixa somente um job ativo por
    version_id + pipeline_version.
    """
    limit_clause = ""
    if limit is not None:
        limit_clause = "LIMIT %s"

    sql = f"""
        WITH eligible AS (
          SELECT v.version_id
          FROM document_versions v
          WHERE v.status = 'INDEXED'
            AND NOT EXISTS (
              SELECT 1
              FROM embedding_chunks c
              WHERE c.version_id = v.version_id
                AND c.pipeline_version = %s
            )
            AND NOT EXISTS (
              SELECT 1
              FROM jobs j
              WHERE j.type = 'IndexVersionJob'
                AND j.status IN {_ACTIVE_JOB_STATUSES_SQL}
                AND j.payload->>'version_id' = v.version_id::text
                AND j.payload->>'pipeline_version' = %s
            )
          ORDER BY v.created_at ASC, v.version_id ASC
          {limit_clause}
        ),
        inserted AS (
          INSERT INTO jobs (
            type,
            payload,
            status,
            idempotency_key
          )
          SELECT
            'IndexVersionJob',
            jsonb_build_object(
              'version_id', e.version_id::text,
              'pipeline_version', %s::text,
              'embedding_model_id', %s::text,
              'force', true
            ),
            'queued',
            (
              'IndexVersionJob:' ||
              %s::text ||
              ':' ||
              e.version_id::text
            )
          FROM eligible e
          ON CONFLICT (type, idempotency_key)
            WHERE idempotency_key IS NOT NULL
              AND status IN ('queued', 'running', 'failed')
          DO NOTHING
          RETURNING job_id
        )
        SELECT
          COUNT(*)::bigint,
          MIN(job_id)::bigint,
          MAX(job_id)::bigint
        FROM inserted
    """

    # pipeline/model parameters are inserted after optional LIMIT param in
    # Python, but SQL placeholders for them occur after the CTE placeholders.
    # Reorder explicitly to keep the statement readable.
    base_params: list[Any] = [pipeline_version, pipeline_version]
    if limit is not None:
        base_params.append(int(limit))
    base_params.extend(
        [pipeline_version, embedding_model_id, pipeline_version]
    )

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, base_params)
            row = cur.fetchone()
        conn.commit()

    enqueued = int(row[0] or 0)
    first_job_id = int(row[1]) if row[1] is not None else None
    last_job_id = int(row[2]) if row[2] is not None else None

    if enqueued > 0:
        # Um NOTIFY por lote é suficiente. A tabela continua sendo a fonte
        # da verdade; não precisamos emitir 1 NOTIFY por job.
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("NOTIFY jobs_channel, 'new_job';")
            conn.commit()

    return {
        "enqueued_jobs": enqueued,
        "first_job_id": first_job_id,
        "last_job_id": last_job_id,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Enfileira reindexação das versões INDEXED que ainda não possuem "
            "chunks no pipeline de destino. O comando é idempotente para jobs "
            "ativos do mesmo version_id + pipeline_version."
        )
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--pipeline-version",
        default=settings.PIPELINE_VERSION,
        help=(
            "Pipeline de destino; permite preparar nova indexação antes do "
            "cutover da API."
        ),
    )
    parser.add_argument(
        "--embedding-model-id",
        default=settings.EMBEDDING_MODEL_ID,
    )
    args = parser.parse_args()

    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit deve ser maior que zero.")

    pipeline_version = args.pipeline_version.strip()
    embedding_model_id = args.embedding_model_id.strip()
    if not pipeline_version:
        raise SystemExit("--pipeline-version não pode ser vazio.")
    if not embedding_model_id:
        raise SystemExit("--embedding-model-id não pode ser vazio.")

    plan = _reindex_plan(pipeline_version, args.limit)
    duplicate_groups = _active_duplicate_groups(pipeline_version)

    payload: dict[str, Any] = {
        "pipeline_version": pipeline_version,
        "embedding_model_id": embedding_model_id,
        "semantic_vocabulary": vocabulary_summary(),
        **plan,
        "active_duplicate_groups": duplicate_groups,
        "execute": bool(args.execute),
    }

    if duplicate_groups > 0:
        payload["warning"] = (
            "Existem grupos duplicados de IndexVersionJob ativos. "
            "Contenha os jobs redundantes antes de continuar."
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        raise SystemExit(2)

    if not args.execute:
        payload["message"] = (
            "Dry-run. Use --execute para enfileirar apenas "
            "eligible_to_enqueue."
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    enqueue_result = _enqueue_missing_versions(
        pipeline_version=pipeline_version,
        embedding_model_id=embedding_model_id,
        limit=args.limit,
    )
    payload.update(enqueue_result)

    # Se outra execução concorrente ganhou a corrida entre plan e INSERT, a
    # unique index simplesmente impede a duplicação. Expor isso no output é
    # útil para auditoria operacional.
    payload["concurrent_conflicts"] = max(
        0,
        int(plan["selected_to_enqueue"])
        - int(enqueue_result["enqueued_jobs"]),
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
