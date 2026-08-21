from __future__ import annotations

import argparse
import json

from intelireg import settings
from intelireg.db import get_conn
from intelireg.jobs import enqueue_job
from intelireg.semantic_vocabulary import vocabulary_summary


def _versions_missing_pipeline(pipeline_version: str, limit: int | None) -> list[str]:
    sql = """
        SELECT v.version_id
        FROM document_versions v
        WHERE v.status = 'INDEXED'
          AND NOT EXISTS (
              SELECT 1
              FROM embedding_chunks c
              WHERE c.version_id = v.version_id
                AND c.pipeline_version = %s
          )
        ORDER BY v.created_at ASC, v.version_id ASC
    """
    params: list[object] = [pipeline_version]
    if limit is not None:
        sql += " LIMIT %s"
        params.append(limit)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return [str(row[0]) for row in cur.fetchall()]


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Enfileira reindexação das versões INDEXED que ainda não possuem "
            "chunks no PIPELINE_VERSION atual."
        )
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--pipeline-version",
        default=settings.PIPELINE_VERSION,
        help="Pipeline de destino; permite preparar nova indexação antes do cutover da API.",
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

    versions = _versions_missing_pipeline(pipeline_version, args.limit)
    payload = {
        "pipeline_version": pipeline_version,
        "embedding_model_id": embedding_model_id,
        "semantic_vocabulary": vocabulary_summary(),
        "version_count": len(versions),
        "execute": bool(args.execute),
    }

    if not args.execute:
        payload["message"] = (
            "Dry-run. Use --execute para enfileirar IndexVersionJob."
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    job_ids: list[int] = []
    for version_id in versions:
        job_ids.append(
            enqueue_job(
                "IndexVersionJob",
                {
                    "version_id": version_id,
                    "pipeline_version": pipeline_version,
                    "embedding_model_id": embedding_model_id,
                    "force": True,
                },
            )
        )

    payload["enqueued_jobs"] = len(job_ids)
    payload["first_job_id"] = job_ids[0] if job_ids else None
    payload["last_job_id"] = job_ids[-1] if job_ids else None
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
