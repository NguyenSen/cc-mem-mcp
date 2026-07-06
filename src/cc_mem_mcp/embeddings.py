"""Embedding backends.

Two providers, chosen by ``EMBEDDING_PROVIDER``:

* ``local``  -> FastEmbed (runs offline, no API key). Default.
* ``openai`` -> OpenAI-compatible embeddings endpoint (needs OPENAI_API_KEY).

Both expose the same tiny interface: ``embed(texts) -> list[vector]`` and a
``dim`` property. The vector dimension is probed once at startup so the Qdrant
collection is created with the right size.
"""

from __future__ import annotations

import logging
from typing import List, Sequence

from .config import Config

log = logging.getLogger("cc-mem-mcp.embeddings")


class Embedder:
    dim: int

    def embed(self, texts: Sequence[str]) -> List[List[float]]:  # pragma: no cover
        raise NotImplementedError


class LocalEmbedder(Embedder):
    def __init__(self, model: str) -> None:
        from fastembed import TextEmbedding  # imported lazily so openai-only users skip it

        log.info("Loading local embedding model: %s", model)
        self._model = TextEmbedding(model_name=model)
        # probe to learn the dimension
        probe = next(iter(self._model.embed(["dimension probe"])))
        self.dim = len(probe)

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        return [list(map(float, v)) for v in self._model.embed(list(texts))]


class OpenAIEmbedder(Embedder):
    # dimensions for the common models; unknown models are probed at runtime
    _KNOWN = {
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
        "text-embedding-ada-002": 1536,
    }

    def __init__(self, model: str, api_key: str | None, base_url: str | None) -> None:
        from openai import OpenAI  # requires the optional `openai` extra

        if not api_key:
            raise ValueError("EMBEDDING_PROVIDER=openai requires OPENAI_API_KEY")
        self._model = model
        self._client = OpenAI(api_key=api_key, base_url=base_url or None)
        self.dim = self._KNOWN.get(model) or len(self.embed(["dimension probe"])[0])

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        resp = self._client.embeddings.create(model=self._model, input=list(texts))
        return [d.embedding for d in resp.data]


def build_embedder(cfg: Config) -> Embedder:
    if cfg.provider == "openai":
        return OpenAIEmbedder(cfg.embedding_model, cfg.openai_api_key, cfg.openai_base_url)
    if cfg.provider == "local":
        return LocalEmbedder(cfg.embedding_model)
    raise ValueError(f"Unknown EMBEDDING_PROVIDER: {cfg.provider!r} (use 'local' or 'openai')")
