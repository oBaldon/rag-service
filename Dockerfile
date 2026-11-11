FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src HF_HOME=/app/storage/hf_cache \
    HF_CACHE_DIR=/app/storage/hf_cache TOKENIZERS_PARALLELISM=false \
    CUDA_VISIBLE_DEVICES="" PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements-docker.lock ./
RUN pip install --no-cache-dir --no-deps --index-url https://download.pytorch.org/whl/cpu torch==2.14.0+cpu \
    && pip install --no-cache-dir -r requirements-docker.lock \
    && groupadd --gid 10001 rag \
    && useradd --uid 10001 --gid rag --create-home rag \
    && mkdir -p /app/storage/hf_cache \
    && chown -R rag:rag /app/storage
COPY --chown=rag:rag src ./src
COPY --chown=rag:rag api ./api
COPY --chown=rag:rag config ./config
COPY --chown=rag:rag docker ./docker
USER rag
EXPOSE 8088
ENTRYPOINT ["python", "docker/entrypoint.py"]
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8088"]
