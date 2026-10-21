#!/bin/sh
set -eu

# FastAPI stays on an internal loopback port; Cloud Run only sees $PORT (Next.js).
uvicorn app.main:app --host 127.0.0.1 --port 8000 --log-level info &

cd /app/web
exec node server.js
