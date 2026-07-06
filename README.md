# cc-mem-mcp

**Lossless, categorized long-term memory for Claude Code (and any MCP client), backed by Qdrant.**

Compaction summaries grow without bound and lose a little more every time they're
re-summarized — after enough rounds, facts *will* be dropped. But Claude Code
**already** produces a categorized, updated state at each compaction (its numbered
summary: *Primary Request, Files and Code Sections, Errors and fixes, Pending
Tasks, …*). This server's job is not to invent its own taxonomy — it is to
**capture that summary the moment it's written and keep it losslessly across
every compaction generation**, so a detail dropped by compaction #7 is still
retrievable from #2.

```
Claude Code writes ~/.claude/projects/<slug>/*.jsonl
        │  (each compaction appends an isCompactSummary line — already categorized)
        ▼
  cc-mem-ingest  ──►  parse numbered sections = categories
        │              split into chunks, content-hash dedup across generations
        ▼
  ┌──────────────────────── Qdrant ────────────────────────┐
  │  embedded local-file (default)  or  shared server (URL) │
  │  payload: category · project · generation · ts          │
  └──────────────────────────────────────────────────────────┘
        ▲
        │  memory_find(query, category?, project?)   ← retrieve on demand
   the agent reloads relevant state instead of trusting the lossy summary
```

The categories are **whatever Claude Code produced** — not an enum we impose.
An optional built-in taxonomy (`code.*` / `business.*`) exists only as a
*suggestion* for the manual `memory_store` path; set `CC_MEM_STRICT_CATEGORIES=1`
if you actually want it enforced.

## Lifecycle: init → auto-update → query

```
memory_init  ──►  scan repo (project.* baseline)  +  fold in current session context
   (once)          + install a managed block in CLAUDE.md so the agent knows to query/update
      │
      ▼
auto-update  ──►  every compaction is captured by a PostCompact hook / watcher (cc-mem-ingest)
      │
      ▼
query        ──►  memory_find(query, category?, project?)   ← agent reloads state on demand
```

**Init** creates the first state and wires Claude Code up in one call:

```bash
cc-mem-init                       # scans cwd, ingests current context, writes CLAUDE.md block
cc-mem-init --install-hooks       # also add SessionStart + PostCompact hooks to settings.json
```

It scans the repo into `project.overview / stack / structure / commands / connections / git / docs`,
derives the Claude Code transcript folder from the repo path to fold in the current
session, and installs a managed `## Long-term Memory` block in `CLAUDE.md` telling the
agent to `memory_find` before re-deriving and to rely on automatic updates. Re-run
anytime — it's idempotent.

## Tools

| Tool | Purpose |
| --- | --- |
| `memory_init(root?, project?, install_claude_md=true, install_hooks=false)` | **Bootstrap.** Scan repo → baseline, fold in current context, install CLAUDE.md guidance. |
| `memory_ingest(project?, session_path?)` | **Auto-update.** Capture Claude Code's compaction summaries from disk. Idempotent. |
| `memory_find(query, category?, project?, limit=5)` | **Query.** Semantic retrieval, filterable by category/project. |
| `memory_store(content, category, project?, tags?, source?)` | Optional manual write-through for a single fact. |
| `memory_categories()` | List the suggestion taxonomy. |
| `memory_delete(id)` | Remove a chunk by id. |
| `memory_stats()` | Collection size, backend, embedding config. |

## Capture: keeping compactions losslessly

Ingestion is **idempotent** (identical chunks re-map to the same id), so run it
however you like:

```bash
# one-shot, current project
cc-mem-ingest --project <transcript-folder-slug>

# background watcher (polls every 30s)
cc-mem-ingest --watch --interval 30

# or wire it to Claude Code's PostCompact hook (fires right after each compaction)
#   settings.json:
#   { "hooks": { "PostCompact": [ { "matcher": "*", "hooks": [
#       { "type": "command", "command": "cc-mem-ingest --once" } ] } ] } }
```

Then, in-session, the agent calls `memory_find` (or `memory_ingest` on demand) to
reload state after a compaction. See [`examples/CLAUDE.snippet.md`](examples/CLAUDE.snippet.md).

## Quick start (Docker)

Build:

```bash
docker build -t cc-mem-mcp .
```

Wire it into Claude Code — add to `.mcp.json` (project) or `~/.claude.json` (global):

```json
{
  "mcpServers": {
    "memory": {
      "command": "docker",
      "args": ["run", "-i", "--rm", "-v", "cc-mem-data:/data", "cc-mem-mcp"]
    }
  }
}
```

That's it — embedded Qdrant persists in the `cc-mem-data` volume, embeddings run
locally via FastEmbed (no API key). See [`examples/`](examples/) for shared-server
and OpenAI variants.

Then paste [`examples/CLAUDE.snippet.md`](examples/CLAUDE.snippet.md) into your
`CLAUDE.md` so the agent writes through and retrieves automatically.

## Configuration

All via environment variables (see [`.env.example`](.env.example)):

| Var | Default | Meaning |
| --- | --- | --- |
| `QDRANT_URL` | *(unset)* | Set to use a shared Qdrant server; unset = embedded local file. |
| `QDRANT_API_KEY` | *(unset)* | API key for a protected server. |
| `QDRANT_PATH` | `/data/qdrant` | Embedded storage path (mount a volume here). |
| `COLLECTION_NAME` | `cc_memory` | Qdrant collection. |
| `EMBEDDING_PROVIDER` | `local` | `local` (FastEmbed) or `openai`. |
| `EMBEDDING_MODEL` | `BAAI/bge-small-en-v1.5` | Model for the chosen provider. |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` | *(unset)* | For `openai` provider. |
| `CC_MEM_CATEGORIES` | *(built-in)* | JSON `{domain:[sub,...]}` to override the taxonomy. |
| `CC_MEM_STRICT_CATEGORIES` | `0` | `1` = reject unknown categories instead of warning. |

## Shared memory across machines/people

Run one Qdrant server (e.g. on a box everyone can reach) and point every client
at it:

```bash
docker compose up -d qdrant           # from this repo
# then in each client's mcp config:
#   -e QDRANT_URL=http://<host>:6333
```

Everyone using the same `QDRANT_URL` + `COLLECTION_NAME` shares one memory.
Keep the same `EMBEDDING_PROVIDER`/`EMBEDDING_MODEL` across clients — vectors
from different models aren't comparable.

## Run without Docker (from source)

```bash
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
# point at your Qdrant (omit for embedded local-file) and run:
QDRANT_URL=http://YOUR_QDRANT_HOST:6333 cc-mem-mcp     # stdio MCP server
```

Wire it into Claude Code with the venv's `cc-mem-mcp` executable as the command,
passing `QDRANT_URL` / `COLLECTION_NAME` / `EMBEDDING_MODEL` via `env`
(see [`examples/`](examples/)).

## Automatic capture (PostCompact hook)

Copy a template from [`hooks/`](hooks/), set your `QDRANT_URL`, and register it in
`.claude/settings.json` so every compaction is captured with no manual step. See
[`hooks/README.md`](hooks/README.md).

## Publish the image (to share with others)

Push a `v*` tag and the bundled GitHub Actions workflow builds and publishes
`ghcr.io/<owner>/cc-mem-mcp` — no secrets to set up:

```bash
git tag v0.1.0 && git push origin v0.1.0
```

Then anyone replaces `OWNER` in the [`examples/`](examples/) `.mcp.json` with your
GitHub owner and they're running the same memory server.

## Multilingual note

The default embedding model is English-centric. For non-English content set a
multilingual model, e.g.:

```
EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
```

Changing the model changes the vector dimension — use a fresh `COLLECTION_NAME`
(or re-index) when you switch.

## Notes

- MCP is stdio JSON-RPC — the client launches the server per session with
  `docker run -i`; it is not a long-running HTTP service.
- All logs go to **stderr**; stdout is reserved for the protocol.
- Switching embedding models changes the vector dimension. Use a fresh
  `COLLECTION_NAME` (or re-index) when you change models.

## License

MIT — see [LICENSE](LICENSE).
