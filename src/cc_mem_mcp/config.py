"""Runtime configuration, resolved from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _default_local_path() -> str:
    # In the Docker image QDRANT_PATH is set to /data/qdrant (a mounted volume).
    # For bare `uvx`/local runs, keep data beside the current working dir.
    return os.getenv("QDRANT_PATH") or os.path.join(os.getcwd(), ".cc-mem", "qdrant")


@dataclass(frozen=True)
class Config:
    # storage
    qdrant_url: str | None
    qdrant_api_key: str | None
    qdrant_path: str
    collection: str
    # embedding
    provider: str            # "local" | "openai"
    embedding_model: str
    openai_api_key: str | None
    openai_base_url: str | None

    @property
    def uses_server(self) -> bool:
        return bool(self.qdrant_url)

    @classmethod
    def from_env(cls) -> "Config":
        provider = (os.getenv("EMBEDDING_PROVIDER") or "local").strip().lower()
        model = os.getenv("EMBEDDING_MODEL") or (
            "text-embedding-3-small" if provider == "openai" else "BAAI/bge-small-en-v1.5"
        )
        return cls(
            qdrant_url=(os.getenv("QDRANT_URL") or "").strip() or None,
            qdrant_api_key=(os.getenv("QDRANT_API_KEY") or "").strip() or None,
            qdrant_path=_default_local_path(),
            collection=(os.getenv("COLLECTION_NAME") or "cc_memory").strip(),
            provider=provider,
            embedding_model=model,
            openai_api_key=(os.getenv("OPENAI_API_KEY") or "").strip() or None,
            openai_base_url=(os.getenv("OPENAI_BASE_URL") or "").strip() or None,
        )
