#!/usr/bin/env bash
set -euo pipefail
set -a
source .env
set +a

exec uvicorn api.main:app --host 127.0.0.1 --port 8088