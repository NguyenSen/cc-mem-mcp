#!/usr/bin/env python
"""Measure whether cc-mem-mcp actually recovers facts compaction would drop.

The whole value proposition is: a fact written into an *old* compaction
generation is still retrievable *now*. This script quantifies that with three
kinds of evidence, cheapest first:

1. Retrieval recall (the metric that matters)
   - ``--auto N``: known-item search. Sample N stored chunks, turn each into a
     de-phrased keyword query (markdown/stopwords stripped so it is NOT a
     verbatim match), search, and record the rank of the source chunk.
     Reports recall@1/@5/@k and MRR, broken down **by generation** — that
     breakdown is the cross-generation losslessness signal.
   - ``--gold FILE``: hand-authored ``{query, expect_substring|expect_id}``
     pairs (JSONL) for high-quality, non-leaky evaluation.

2. Coverage / anti-bloat (free, from payloads)
   - points per generation + distinct generations: shows capture is running
     for recent sessions and that dedup keeps growth sub-linear.

3. Latency (free)
   - p50/p95 of ``find()``.

Reuses the package's own Config/embedder/store so the embedding model and
collection match production exactly. Point it at your Qdrant the same way you
point the server:

    QDRANT_URL=http://HOST:6333 COLLECTION_NAME=cc_memory \
    EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 \
    python -m eval.recall_eval --auto 150 --k 10

    python -m eval.recall_eval --gold eval/gold.example.jsonl --k 5
"""

from __future__ import annotations

import argparse
import json
import random
import re
import statistics
import sys
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from cc_mem_mcp.config import Config
from cc_mem_mcp.embeddings import build_embedder
from cc_mem_mcp.store import MemoryStore

# ---------------------------------------------------------------------------
# query construction: degrade a stored chunk into a realistic paraphrased query
# ---------------------------------------------------------------------------

# tiny bilingual stopword set — enough to stop queries from being verbatim copies
_STOP = set(
    """
a an the of to in on for and or is are was were be been being this that these those
with without from into as at by it its it's not no yes do does did done have has had
i you he she we they them him her my your our their me us will would can could should
va co la cua cho khi nen thi da dang se khong chua duoc cai nay do voi tu theo ra vao
mot cac nhung tai boi vi nhu de con nua rat chi moi hay hoac neu ma o len xuong
""".split()
)

_CODE_FENCE = re.compile(r"```.*?```", re.DOTALL)
_WORD = re.compile(r"[0-9A-Za-zÀ-ỹ_./:-]{2,}", re.UNICODE)


def make_query(content: str, max_terms: int = 14) -> str:
    """Turn a stored chunk into a de-phrased keyword query.

    Strips code fences and markdown, drops stopwords/punctuation, keeps the
    salient tokens in order. The result shares the chunk's *terms* but not its
    exact wording — a fair proxy for "user asks about this fact later".
    """
    text = _CODE_FENCE.sub(" ", content)
    text = text.replace("`", " ").replace("*", " ").replace("#", " ")
    toks = _WORD.findall(text)
    kept: List[str] = []
    seen: set = set()
    for t in toks:
        low = t.lower()
        if low in _STOP or len(t) < 2:
            continue
        if low in seen:  # de-dup keeps the query terse and less leaky
            continue
        seen.add(low)
        kept.append(t)
        if len(kept) >= max_terms:
            break
    return " ".join(kept)


def generation_of(payload: Dict[str, Any]) -> str:
    """Best-effort generation label: source ``compaction:<sid>#<N>`` -> N."""
    src = payload.get("source") or ""
    m = re.search(r"#(\d+)\s*$", str(src))
    if m:
        return m.group(1)
    gen = payload.get("generation")
    if gen not in (None, ""):
        return str(gen)
    if str(src).startswith("init:"):
        return "init"
    return "?"


# ---------------------------------------------------------------------------
# data access
# ---------------------------------------------------------------------------

def scroll_all(store: MemoryStore, project: Optional[str], cap: int) -> List[Tuple[str, Dict[str, Any]]]:
    """Page through the collection, returning (id, payload) up to ``cap``."""
    out: List[Tuple[str, Dict[str, Any]]] = []
    offset = None
    from qdrant_client.http import models as qm

    flt = None
    if project:
        flt = qm.Filter(must=[qm.FieldCondition(key="project", match=qm.MatchValue(value=project))])
    while len(out) < cap:
        points, offset = store.client.scroll(
            collection_name=store.cfg.collection,
            scroll_filter=flt,
            limit=min(256, cap - len(out)),
            with_payload=True,
            with_vectors=False,
            offset=offset,
        )
        if not points:
            break
        for p in points:
            out.append((str(p.id), p.payload or {}))
        if offset is None:
            break
    return out


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------

def _reciprocal_rank(rank: Optional[int]) -> float:
    return 1.0 / rank if rank else 0.0


def summarize(ranks: List[Optional[int]], k: int) -> Dict[str, Any]:
    n = len(ranks)
    if n == 0:
        return {"n": 0}
    hit1 = sum(1 for r in ranks if r == 1)
    hit5 = sum(1 for r in ranks if r and r <= 5)
    hitk = sum(1 for r in ranks if r and r <= k)
    mrr = sum(_reciprocal_rank(r) for r in ranks) / n
    return {
        "n": n,
        "recall@1": round(hit1 / n, 3),
        "recall@5": round(hit5 / n, 3),
        f"recall@{k}": round(hitk / n, 3),
        "mrr": round(mrr, 3),
        "misses": n - hitk,
    }


def _bar(frac: float, width: int = 24) -> str:
    fill = int(round(frac * width))
    return "█" * fill + "·" * (width - fill)


# ---------------------------------------------------------------------------
# eval modes
# ---------------------------------------------------------------------------

def run_auto(store: MemoryStore, n: int, k: int, project: Optional[str], seed: int) -> None:
    pool = scroll_all(store, project, cap=max(n * 4, n))
    if not pool:
        print("!! collection empty (or project filter matched nothing) — nothing to evaluate")
        return
    rnd = random.Random(seed)
    sample = pool if len(pool) <= n else rnd.sample(pool, n)

    ranks: List[Optional[int]] = []
    by_gen: Dict[str, List[Optional[int]]] = defaultdict(list)
    by_cat: Dict[str, List[Optional[int]]] = defaultdict(list)
    latencies: List[float] = []
    sanity_ok = 0
    sanity_n = 0

    for i, (pid, payload) in enumerate(sample):
        content = payload.get("content") or ""
        query = make_query(content)
        if len(query.split()) < 3:  # too little signal to be a fair query
            continue
        t0 = time.perf_counter()
        hits = store.find(query, project=project, limit=k)
        latencies.append((time.perf_counter() - t0) * 1000)
        rank = next((idx + 1 for idx, h in enumerate(hits) if h["id"] == pid), None)
        ranks.append(rank)
        by_gen[generation_of(payload)].append(rank)
        by_cat[str(payload.get("category") or "?")].append(rank)

        if i % 5 == 0:  # sanity: exact content must retrieve itself at rank 1
            sanity_n += 1
            ex = store.find(content, project=project, limit=1)
            if ex and ex[0]["id"] == pid:
                sanity_ok += 1

    print("\n=== AUTO known-item recall ===")
    print(f"sampled {len(ranks)} chunks · k={k} · seed={seed}"
          + (f" · project={project}" if project else ""))
    overall = summarize(ranks, k)
    for key, val in overall.items():
        print(f"  {key:>10}: {val}")
    if sanity_n:
        print(f"  self-retrieval sanity: {sanity_ok}/{sanity_n} exact queries rank-1"
              + ("  (OK)" if sanity_ok == sanity_n else "  (!! index/model mismatch)"))

    print("\n--- cross-generation recall (the losslessness signal) ---")
    print("  older generations still scoring = facts survive compaction")
    for gen in sorted(by_gen, key=lambda g: (g.isdigit(), g)):
        s = summarize(by_gen[gen], k)
        if s["n"]:
            print(f"  gen {gen:>4} | n={s['n']:>3} | recall@{k}={s[f'recall@{k}']:.2f} "
                  f"{_bar(s[f'recall@{k}'])} mrr={s['mrr']:.2f}")

    print("\n--- recall by category (top 10 by volume) ---")
    top = sorted(by_cat.items(), key=lambda kv: -len(kv[1]))[:10]
    for cat_name, rs in top:
        s = summarize(rs, k)
        print(f"  {cat_name:<34} n={s['n']:>3} recall@{k}={s[f'recall@{k}']:.2f} mrr={s['mrr']:.2f}")

    if latencies:
        latencies.sort()
        p50 = statistics.median(latencies)
        p95 = latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))]
        print(f"\n--- latency --- p50={p50:.0f}ms p95={p95:.0f}ms (n={len(latencies)})")


def run_gold(store: MemoryStore, path: str, k: int, project: Optional[str]) -> None:
    cases: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            cases.append(json.loads(line))
    if not cases:
        print(f"!! no cases in {path}")
        return

    ranks: List[Optional[int]] = []
    latencies: List[float] = []
    print("\n=== GOLD set ===")
    print(f"{path} · {len(cases)} cases · k={k}\n")
    for c in cases:
        q = c["query"]
        want_id = c.get("expect_id")
        want_sub = (c.get("expect_substring") or "").lower()
        proj = c.get("project", project)
        t0 = time.perf_counter()
        hits = store.find(q, category=c.get("category"), project=proj, limit=k)
        latencies.append((time.perf_counter() - t0) * 1000)

        rank = None
        for idx, h in enumerate(hits):
            ok_id = want_id and h["id"] == want_id
            ok_sub = want_sub and want_sub in (h.get("content") or "").lower()
            if ok_id or ok_sub:
                rank = idx + 1
                break
        ranks.append(rank)
        mark = f"rank {rank}" if rank else "MISS"
        top = (hits[0]["content"][:60] + "…") if hits else "(no hits)"
        print(f"  [{mark:>7}] {q[:52]:<52} | top: {top}")

    print()
    for key, val in summarize(ranks, k).items():
        print(f"  {key:>10}: {val}")
    if latencies:
        print(f"  latency p50={statistics.median(latencies):.0f}ms")


def print_coverage(store: MemoryStore, project: Optional[str]) -> None:
    pool = scroll_all(store, project, cap=100_000)
    by_gen: Dict[str, int] = defaultdict(int)
    by_proj: Dict[str, int] = defaultdict(int)
    for _pid, payload in pool:
        by_gen[generation_of(payload)] += 1
        by_proj[str(payload.get("project") or "?")] += 1
    print("\n=== COVERAGE / anti-bloat ===")
    print(f"total points: {len(pool)}"
          + (f" (project={project})" if project else "")
          + f" · distinct generations: {sum(1 for g in by_gen if g not in ('?','init'))}")
    print("  points per generation:")
    for gen in sorted(by_gen, key=lambda g: (g.isdigit(), g)):
        print(f"    gen {gen:>4}: {by_gen[gen]:>4}")
    if len(by_proj) > 1:
        print("  points per project:")
        for p, c in sorted(by_proj.items(), key=lambda kv: -kv[1]):
            print(f"    {p:<28} {c:>4}")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Evaluate cc-mem-mcp retrieval effectiveness.")
    ap.add_argument("--auto", type=int, metavar="N", help="known-item recall over N sampled chunks")
    ap.add_argument("--gold", metavar="FILE", help="gold JSONL of {query, expect_substring|expect_id}")
    ap.add_argument("--k", type=int, default=10, help="top-k cutoff (default 10)")
    ap.add_argument("--project", default=None, help="restrict to one project label")
    ap.add_argument("--seed", type=int, default=1234, help="sampling seed (default 1234)")
    ap.add_argument("--no-coverage", action="store_true", help="skip the coverage report")
    args = ap.parse_args(argv)

    if not args.auto and not args.gold:
        args.auto = 100  # sensible default

    cfg = Config.from_env()
    print(f"Qdrant: {cfg.qdrant_url or cfg.qdrant_path} · collection={cfg.collection}")
    print(f"Embedding: {cfg.provider}:{cfg.embedding_model}")
    store = MemoryStore(cfg, build_embedder(cfg))

    if not args.no_coverage:
        print_coverage(store, args.project)
    if args.auto:
        run_auto(store, args.auto, args.k, args.project, args.seed)
    if args.gold:
        run_gold(store, args.gold, args.k, args.project)
    return 0


if __name__ == "__main__":
    sys.exit(main())
