"""Qdrant-backed memory store.

Uses the embedded, local-file Qdrant when ``QDRANT_URL`` is unset (zero setup,
data persists under ``QDRANT_PATH``), or a shared Qdrant server when it is set.
Payload carries the category (``domain``/``sub``), project, tags and a UTC
timestamp so retrieval can filter by category/project.
"""

from __future__ import annotations

import hashlib
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

from . import categories as cat
from .config import Config
from .embeddings import Embedder

log = logging.getLogger("cc-mem-mcp.store")

# Stable namespace so identical chunks map to the same point id across runs
# (idempotent upsert => unchanged content de-duplicates instead of piling up).
_NS = uuid.UUID("6f9b8e2a-1c4d-4a7b-9e3f-b7d1e2000001")


def content_hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]


class MemoryStore:
    def __init__(self, cfg: Config, embedder: Embedder) -> None:
        self.cfg = cfg
        self.embedder = embedder
        if cfg.uses_server:
            log.info("Connecting to Qdrant server at %s", cfg.qdrant_url)
            self.client = QdrantClient(url=cfg.qdrant_url, api_key=cfg.qdrant_api_key)
        else:
            os.makedirs(cfg.qdrant_path, exist_ok=True)
            log.info("Using embedded Qdrant at %s", cfg.qdrant_path)
            self.client = QdrantClient(path=cfg.qdrant_path)
        self._ensure_collection()

    def _ensure_collection(self) -> None:
        name = self.cfg.collection
        if not self.client.collection_exists(name):
            self.client.create_collection(
                collection_name=name,
                vectors_config=qm.VectorParams(size=self.embedder.dim, distance=qm.Distance.COSINE),
            )
            # payload indexes make category/project filters fast
            for field in ("domain", "sub", "category", "project"):
                try:
                    self.client.create_payload_index(
                        collection_name=name,
                        field_name=field,
                        field_schema=qm.PayloadSchemaType.KEYWORD,
                    )
                except Exception as exc:  # noqa: BLE001 - index is best-effort
                    log.debug("payload index %s skipped: %s", field, exc)

    # ---- writes -----------------------------------------------------------
    def store(
        self,
        content: str,
        category: str,
        project: Optional[str] = None,
        tags: Optional[List[str]] = None,
        source: Optional[str] = None,
    ) -> Dict[str, Any]:
        domain, sub = cat.split_category(category)
        norm = cat.normalize_category(category)
        known = cat.is_known(category)
        if not known:
            msg = f"Unknown category {norm!r}; valid: {', '.join(cat.flat_list())}"
            if cat.strict():
                raise ValueError(msg)
            log.warning(msg)

        vector = self.embedder.embed([self.cfg.passage_prefix + content])[0]
        point_id = str(uuid.uuid4())
        payload = {
            "content": content,
            "category": norm,
            "domain": domain,
            "sub": sub,
            "project": project,
            "tags": tags or [],
            "source": source,
            "ts": datetime.now(timezone.utc).isoformat(),
            "known_category": known,
        }
        self.client.upsert(
            collection_name=self.cfg.collection,
            points=[qm.PointStruct(id=point_id, vector=vector, payload=payload)],
        )
        return {"id": point_id, "category": norm, "known_category": known}

    def add_many(self, items: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        """Bulk-upsert chunks with content-hash dedup.

        Each item: {content, category, project?, generation?, source?, ts?, tags?}.
        The point id is derived from (project, category, content) so an identical
        chunk seen in a later compaction re-upserts to the SAME id — unchanged
        text de-duplicates while genuinely new/changed text is added. This is what
        makes capture across N compactions lossless without N-fold blow-up.
        """
        if not items:
            return {"received": 0, "unique": 0, "embedded": 0}

        # collapse exact duplicates within this batch first
        seen: Dict[str, Dict[str, Any]] = {}
        for it in items:
            content = (it.get("content") or "").strip()
            if not content:
                continue
            norm = cat.normalize_category(it.get("category") or "")
            key = f"{it.get('project') or ''}|{norm}|{content_hash(content)}"
            if key not in seen:
                seen[key] = {**it, "content": content, "category": norm, "_key": key}
        uniq = list(seen.values())
        if not uniq:
            return {"received": len(items), "unique": 0, "embedded": 0, "skipped_existing": 0}

        # assign deterministic ids, then skip anything already stored so a
        # recurring ingest (hook/watcher) only embeds the delta.
        for u in uniq:
            u["_id"] = str(uuid.uuid5(_NS, u["_key"]))
        existing: set = set()
        try:
            got = self.client.retrieve(collection_name=self.cfg.collection,
                                       ids=[u["_id"] for u in uniq],
                                       with_payload=False, with_vectors=False)
            existing = {str(p.id) for p in got}
        except Exception as exc:  # noqa: BLE001 - fall back to embedding all
            log.debug("existence check skipped: %s", exc)
        fresh = [u for u in uniq if u["_id"] not in existing]
        if not fresh:
            return {"received": len(items), "unique": 0, "embedded": 0, "skipped_existing": len(uniq)}

        vectors = self.embedder.embed([self.cfg.passage_prefix + u["content"] for u in fresh])
        points: List[qm.PointStruct] = []
        now = datetime.now(timezone.utc).isoformat()
        for u, vec in zip(fresh, vectors):
            domain, sub = cat.split_category(u["category"])
            pid = u["_id"]
            points.append(
                qm.PointStruct(
                    id=pid,
                    vector=vec,
                    payload={
                        "content": u["content"],
                        "category": u["category"],
                        "domain": domain,
                        "sub": sub,
                        "project": u.get("project"),
                        "tags": u.get("tags") or [],
                        "source": u.get("source") or "ingest",
                        "generation": u.get("generation"),
                        "ts": u.get("ts") or now,
                    },
                )
            )
        self.client.upsert(collection_name=self.cfg.collection, points=points)
        return {"received": len(items), "unique": len(points), "embedded": len(points),
                "skipped_existing": len(uniq) - len(fresh)}

    def delete(self, point_id: str) -> Dict[str, Any]:
        self.client.delete(
            collection_name=self.cfg.collection,
            points_selector=qm.PointIdsList(points=[point_id]),
        )
        return {"deleted": point_id}

    # ---- reads ------------------------------------------------------------
    def find(
        self,
        query: str,
        category: Optional[str] = None,
        project: Optional[str] = None,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        conditions: List[qm.FieldCondition] = []
        if category:
            domain, sub = cat.split_category(category)
            if sub and domain:
                conditions.append(qm.FieldCondition(key="category", match=qm.MatchValue(value=f"{domain}.{sub}")))
            elif domain:  # whole domain, e.g. "code"
                conditions.append(qm.FieldCondition(key="domain", match=qm.MatchValue(value=domain)))
            elif sub:
                conditions.append(qm.FieldCondition(key="sub", match=qm.MatchValue(value=sub)))
        if project:
            conditions.append(qm.FieldCondition(key="project", match=qm.MatchValue(value=project)))
        flt = qm.Filter(must=conditions) if conditions else None

        vector = self.embedder.embed([self.cfg.query_prefix + query])[0]
        hits = self.client.query_points(
            collection_name=self.cfg.collection,
            query=vector,
            query_filter=flt,
            limit=max(1, int(limit)),
            with_payload=True,
        ).points
        results: List[Dict[str, Any]] = []
        for h in hits:
            p = h.payload or {}
            results.append(
                {
                    "id": str(h.id),
                    "score": round(float(h.score), 4),
                    "content": p.get("content"),
                    "category": p.get("category"),
                    "project": p.get("project"),
                    "tags": p.get("tags", []),
                    "source": p.get("source"),
                    "ts": p.get("ts"),
                }
            )
        return results

    def stats(self) -> Dict[str, Any]:
        info = self.client.count(collection_name=self.cfg.collection, exact=True)
        return {
            "collection": self.cfg.collection,
            "count": info.count,
            "backend": "server" if self.cfg.uses_server else "embedded",
            "location": self.cfg.qdrant_url or self.cfg.qdrant_path,
            "embedding_provider": self.cfg.provider,
            "embedding_model": self.cfg.embedding_model,
            "dim": self.embedder.dim,
        }
