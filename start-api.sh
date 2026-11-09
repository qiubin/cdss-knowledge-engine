#!/usr/bin/env bash
# 启动 CDSS 知识引擎 API 服务（macOS / Linux）
# 用法:
#   ./start-api.sh              # 默认 127.0.0.1:8001
#   PORT=8080 ./start-api.sh    # 自定义端口
#   PYTHON=/path/to/venv/bin/python ./start-api.sh
#   RELOAD=1 ./start-api.sh     # 开发模式热重载
set -euo pipefail

cd "$(dirname "$0")"

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8001}"
PYTHON="${PYTHON:-python3}"

export PYTHONPATH=.

echo "CDSS 知识引擎 -> http://${HOST}:${PORT}"
exec "$PYTHON" -B -m uvicorn src.api.main:app \
  --host "$HOST" \
  --port "$PORT" \
  ${RELOAD:+--reload}
