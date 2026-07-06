# cc-mem-mcp — categorized write-through memory MCP server
FROM python:3.12-slim

# Build arg: bake the default local embedding model into the image so the
# first `memory_store` call is instant and works fully offline. Set to 0 to
# skip (smaller image, model downloaded on first use).
ARG PREFETCH_MODEL=1
ARG DEFAULT_MODEL=BAAI/bge-small-en-v1.5

ENV PYTHONUNBUFFERED=1 \
    QDRANT_PATH=/data/qdrant \
    EMBEDDING_PROVIDER=local

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src

# Install the package plus the optional OpenAI extra so either provider works
# from the same image.
RUN pip install --no-cache-dir ".[openai]"

# Warm the FastEmbed model cache at build time (optional).
RUN if [ "$PREFETCH_MODEL" = "1" ]; then \
      python -c "from fastembed import TextEmbedding; import itertools; \
list(itertools.islice(TextEmbedding(model_name='${DEFAULT_MODEL}').embed(['warm']), 1))"; \
    fi

# Embedded Qdrant data persists here — mount a named volume to keep memory.
VOLUME /data

# The server talks JSON-RPC over stdio; clients launch it with `docker run -i`.
ENTRYPOINT ["cc-mem-mcp"]
