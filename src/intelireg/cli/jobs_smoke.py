import os
from intelireg.jobs import enqueue_job, fetch_next_job, mark_done

def main():
    worker_id = os.getenv("WORKER_ID", "local-dev-1")

    job_id = enqueue_job("IndexVersionJob", {
        "version_id": "00000000-0000-0000-0000-000000000000",
        "pipeline_version": "mvp-v1",
        "embedding_model_id": "text-embedding-3-small@1536"
    })
    print(f"enqueued job_id={job_id}")

    job = fetch_next_job(worker_id=worker_id)
    if not job:
        raise SystemExit("nenhum job encontrado (inesperado)")

    print(f"fetched job_id={job.job_id} type={job.type} payload={job.payload}")

    mark_done(job.job_id)
    print(f"done job_id={job.job_id}")

if __name__ == "__main__":
    main()
