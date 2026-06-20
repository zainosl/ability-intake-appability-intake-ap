#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
PY="/Users/zain/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
PORT="${PORT:-8787}" "$PY" ai_intake_app/app.py
