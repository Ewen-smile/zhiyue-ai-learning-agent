#!/bin/bash
set -e
cd "$(dirname "$0")"
PYTHON="${PYTHON:-python3}"
if ! "$PYTHON" -c "import fastapi,uvicorn,httpx,pydantic" >/dev/null 2>&1; then
  echo "需要安装 Python 运行依赖。"
  "$PYTHON" -m pip install -r requirements.txt
fi
printf '\n知跃 AI 已准备启动。请在浏览器打开 http://127.0.0.1:8000\n\n'
exec "$PYTHON" app.py
