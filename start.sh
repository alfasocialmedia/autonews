#!/bin/bash
set -e

# Arrancar el worker en background
python -m app.worker &
WORKER_PID=$!
echo "[AutoNews] Worker iniciado (PID $WORKER_PID)"

# Arrancar uvicorn en foreground
exec uvicorn app.main:app --host 0.0.0.0 --port 3000
