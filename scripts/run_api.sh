#!/usr/bin/env bash
set -euo pipefail
set -a
source .env
set +a

exec uvicorn api.main:app --host 0.0.0.0 --port 8088