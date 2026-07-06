@echo off
REM Copy to run.cmd, set your Qdrant host, and run after each session as a
REM lightweight retrieval-recall regression. run.cmd is gitignored.
if "%QDRANT_URL%"=="" set "QDRANT_URL=http://YOUR_QDRANT_HOST:6333"
if "%COLLECTION_NAME%"=="" set "COLLECTION_NAME=cc_memory"
if "%EMBEDDING_PROVIDER%"=="" set "EMBEDDING_PROVIDER=local"
if "%EMBEDDING_MODEL%"=="" set "EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
if "%PY%"=="" set "PY=python"

pushd "%~dp0.."
"%PY%" -m eval.recall_eval --auto 150 --k 10 --metrics eval/metrics.jsonl %*
"%PY%" -m eval.recall_eval --gold eval/gold.server-deploy.jsonl --k 5 --no-coverage --metrics eval/metrics.jsonl
popd
