#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
PORT="${PORT:-8788}"; HOST="${HOST:-127.0.0.1}"
PY=$(command -v python3 || command -v python)
echo "启动： http://$HOST:$PORT  （Ctrl+C 停止）"
exec "$PY" -m uvicorn app.main:app --host "$HOST" --port "$PORT"
