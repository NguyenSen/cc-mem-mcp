#!/usr/bin/env python
"""Re-embed an existing collection into a new one with a different model.

Isolates the *embedding model* variable for A/B recall tests: it reuses the
exact same chunks (content + payload + ids) from the source collection and only
swaps the vectors, so any recall difference is attributable to the model, not to
re-chunking. Nothing touches the source collection.

    QDRANT_URL=http://HOST:6333 COLLECTION_NAME=cc_memory \
    python -m eval.reembed --target-collection cc_memory_m3 --target-model BAAI/bge-m3

Then evaluate the copy:

    COLLECTION_NAME=cc_memory_m3 EMBEDDING_MODEL=BAAI/bge-m3 \
    python -m eval.recall_eval --gold eval/gold.server-deploy.jsonl --k 5
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

from cc_mem_mcp.config import Config
from cc_mem_mcp.embeddings import build_embedder


def scroll_all(client: QdrantClient, collection: str) -> List[Tuple[str, Dict[str, Any]]]:
    out: List[Tuple[str, Dict[str, Any]]] = []
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=collection, limit=256,
            with_payload=True, with_vectors=False, offset=offset,
        )
        out.extend((str(p.id), p.payload or {}) for p in points)
        if offset is None:
            break
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Re-embed a collection with a different model (A/B).")
    ap.add_argument("--target-collection", required=True)
    ap.add_argument("--target-model", required=True)
    ap.add_argument("--target-provider", default=None, help="default: same provider as source env")
    ap.add_argument("--passage-prefix", default="",
                    help="prepended to each chunk before embedding (e5 needs 'passage: ')")
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--recreate", action="store_true", help="drop target collection first")
    args = ap.parse_args(argv)

    src = Config.from_env()
    client = QdrantClient(url=src.qdrant_url, api_key=src.qdrant_api_key) if src.uses_server \
        else QdrantClient(path=src.qdrant_path)

    rows = scroll_all(client, src.collection)
    print(f"source {src.collection}: {len(rows)} points")
    if not rows:
        print("!! nothing to re-embed")
        return 1

    # build the target embedder by cloning env config with the new model/provider
    tgt_cfg = Config(
        qdrant_url=src.qdrant_url, qdrant_api_key=src.qdrant_api_key,
        qdrant_path=src.qdrant_path, collection=args.target_collection,
        provider=(args.target_provider or src.provider),
        embedding_model=args.target_model,
        openai_api_key=src.openai_api_key, openai_base_url=src.openai_base_url,
        query_prefix="", passage_prefix="",  # reembed applies --passage-prefix itself
    )
    print(f"target model: {tgt_cfg.provider}:{tgt_cfg.embedding_model} (loading…)")
    embedder = build_embedder(tgt_cfg)
    print(f"target dim: {embedder.dim}")

    if args.recreate and client.collection_exists(args.target_collection):
        client.delete_collection(args.target_collection)
    if not client.collection_exists(args.target_collection):
        client.create_collection(
            collection_name=args.target_collection,
            vectors_config=qm.VectorParams(size=embedder.dim, distance=qm.Distance.COSINE),
        )
        for field in ("domain", "sub", "category", "project"):
            try:
                client.create_payload_index(collection_name=args.target_collection,
                                             field_name=field,
                                             field_schema=qm.PayloadSchemaType.KEYWORD)
            except Exception:  # noqa: BLE001
                pass

    t0 = time.perf_counter()
    done = 0
    for i in range(0, len(rows), args.batch):
        chunk = rows[i:i + args.batch]
        texts = [args.passage_prefix + (p.get("content") or "") for _id, p in chunk]
        vecs = embedder.embed(texts)
        client.upsert(
            collection_name=args.target_collection,
            points=[qm.PointStruct(id=pid, vector=v, payload=pl)
                    for (pid, pl), v in zip(chunk, vecs)],
        )
        done += len(chunk)
        print(f"  {done}/{len(rows)} re-embedded", end="\r", flush=True)
    dt = time.perf_counter() - t0
    print(f"\ndone: {done} points in {dt:.1f}s "
          f"({done / dt:.1f}/s) -> collection {args.target_collection}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
