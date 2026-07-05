#!/bin/sh
# Project Athena  -  one-shot LLM model pull.
#
# This script is mounted into a one-shot init container that shares the
# `ollama_data` volume with the long-lived `ollama` service. We wait for
# the Ollama HTTP API to be ready, then pull the model. Once the pull
# completes the container exits; the next `docker compose up` will see
# the model already in the volume and skip the pull.
#
# Env (set in docker-compose.yml):
#   ATHENA_OLLAMA_MODEL  default: qwen2.5:1.5b-instruct
#   OLLAMA_HOST          default: http://ollama:11434  (set in compose)

set -eu

MODEL="${ATHENA_OLLAMA_MODEL:-qwen2.5:1.5b-instruct}"
HOST="${OLLAMA_HOST:-http://ollama:11434}"

echo "[ollama-pull] waiting for ${HOST} to be ready…"
# /api/tags returns 200 once ollama is serving.
i=0
until curl -fsS "${HOST}/api/tags" >/dev/null 2>&1; do
    i=$((i + 1))
    if [ "$i" -gt 60 ]; then
        echo "[ollama-pull] ollama did not come up in 60s; aborting"
        exit 1
    fi
    sleep 2
done
echo "[ollama-pull] ollama is up"

# Skip the pull if the model is already present.
if curl -fsS "${HOST}/api/tags" | grep -q "\"name\":\"${MODEL}\""; then
    echo "[ollama-pull] ${MODEL} already present; nothing to do"
    exit 0
fi

echo "[ollama-pull] pulling ${MODEL} (this may take a few minutes)…"
ollama pull "${MODEL}"
echo "[ollama-pull] done"
