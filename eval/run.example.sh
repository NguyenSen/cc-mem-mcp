#!/usr/bin/env bash
# Copy to run.sh, set your Qdrant host, and run after each session as a
# lightweight retrieval-recall regression. run.sh is gitignored (holds your host).
set -euo pipefail

export QDRANT_URL="${QDRANT_URL:-http://YOUR_QDRANT_HOST:6333}"
export COLLECTION_NAME="${COLLECTION_NAME:-cc_memory}"
export EMBEDDING_PROVIDER="${EMBEDDING_PROVIDER:-local}"
export EMBEDDING_MODEL="${EMBEDDING_MODEL:-sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2}"
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8

cd "$(dirname "$0")/.."
PY="${PY:-python}"   # or point at your venv, e.g. PY=./.venv/Scripts/python.exe

# best-case upper bound (known-item) + real-case gold set + coverage report
"$PY" -m eval.recall_eval --auto "${AUTO:-150}" --k "${K:-10}" "$@"
"$PY" -m eval.recall_eval --gold "${GOLD:-eval/gold.server-deploy.jsonl}" --k 5 --no-coverage
