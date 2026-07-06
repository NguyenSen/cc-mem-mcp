# Evaluating cc-mem-mcp

**Question this answers:** does the memory actually recover facts that
compaction would otherwise drop — or is it just a growing pile of vectors?

The tool's only claim is *cross-generation recall*: a fact written into an old
compaction summary is still retrievable now. Everything here measures that,
cheapest evidence first.

## Run it

Point it at the same Qdrant/model as your server (env vars are read identically):

```bash
pip install -e .          # from repo root, so `cc_mem_mcp` imports

QDRANT_URL=http://YOUR_HOST:6333 \
COLLECTION_NAME=cc_memory \
EMBEDDING_PROVIDER=local \
EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 \
python -m eval.recall_eval --auto 150 --k 10
```

Gold-set mode (hand-written questions — the most honest signal):

```bash
python -m eval.recall_eval --gold eval/gold.example.jsonl --k 5
```

Restrict to one project: `--project Server-Deploy`.

## What it reports

### 1. Retrieval recall — the metric that matters

- **`--auto N`** (known-item search): samples N stored chunks, turns each into a
  *de-phrased* keyword query (code fences, markdown and stopwords stripped, so it
  shares the chunk's terms but not its exact wording), searches, and records the
  rank of the source chunk.
  - `recall@1 / @5 / @k` — fraction whose source fact came back in the top-k.
  - `mrr` — mean reciprocal rank (rewards putting the right fact near the top).
  - **cross-generation breakdown** — recall bucketed by compaction generation.
    This is the headline: if `gen 2` still scores well when you're on `gen 8`,
    the memory is losslessly carrying old facts. A generation that reads `n=0`
    (or is missing entirely) means **capture never ran for that session** — a
    broken/absent PostCompact hook shows up here, not as a crash.
  - **self-retrieval sanity** — querying a chunk with its *exact* text must return
    itself at rank 1. If that fails, the index or embedding model is mismatched
    (e.g. vectors from a different model), and every other number is meaningless.

- **`--gold FILE`**: hand-authored `{query, expect_substring | expect_id}` pairs.
  Known-item recall is convenient but slightly optimistic (the query is derived
  from the answer). Gold cases you write from memory of an earlier session are the
  unbiased test. Keep a growing `gold.jsonl` as your regression suite.

> Known-item recall is an **upper bound** — real user questions are phrased
> further from the stored text, so treat auto numbers as "best case" and trust
> the gold set for "real case". Report both.

### 2. Coverage / anti-bloat (free, from payloads)

`points per generation` and `distinct generations`. Two things to check:

- **Capture is live**: the most recent session's generation should be present and
  non-empty. A gap = the hook/watcher isn't running.
- **Dedup works**: points should grow **sub-linearly** in the number of
  compactions. If every re-ingest adds a full copy, the content-hash dedup is
  broken — you'd see points-per-generation roughly constant *and* the total
  ballooning on re-runs.

### 3. Latency (free)

`p50 / p95` of `find()` — a query the agent makes mid-task, so it should stay in
the low hundreds of ms against a server backend.

## Reading the numbers

| Signal | Good | Bad → likely cause |
| --- | --- | --- |
| self-retrieval sanity | all rank-1 | any miss → wrong `EMBEDDING_MODEL` vs the one that wrote the vectors |
| recall@k (auto) | high & flat across generations | drops for old gens → summaries genuinely losing detail / re-chunked ids |
| a generation missing / `n=0` | every recent session present | gap → PostCompact hook not firing (check `.claude/settings.json`) |
| points per generation | small, sub-linear growth | constant-and-total-ballooning → dedup broken |
| gold recall ≪ auto recall | close-ish | huge gap → queries need better chunking or a stronger embedding model |

## Caveats

- No ground-truth relevance labels beyond your gold set — auto mode assumes the
  *source* chunk is the one true answer, which under-counts cases where another
  chunk is an equally good answer. It measures known-item findability, not full
  ranking quality.
- Recall is embedding-bound: short, keyword-poor facts ("drive-case slug bug")
  retrieve worse than distinctive strings. If recall is low, try a stronger
  `EMBEDDING_MODEL` before concluding the capture is at fault.
