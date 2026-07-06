<!-- Paste into your project CLAUDE.md (or ~/.claude/CLAUDE.md) to use the
     `memory` MCP server as lossless long-term memory across compactions. -->

## Long-term Memory (`memory` MCP server)

Claude Code's compaction summary is lossy across many rounds. The `memory` MCP
server captures each compaction and keeps it losslessly, so treat it — not the
in-context summary — as the source of truth for earlier context.

**Capture (automatic):** compaction summaries are ingested from disk by
`cc-mem-ingest` (via a PostCompact hook or a background watcher). You normally
don't call anything to write; if you suspect a capture was missed, call
`memory_ingest` to sync now.

**Recall (do this often):** after a compaction, or whenever you're about to
re-derive something that was established earlier in the session, call
`memory_find` with a query. Narrow it with `project` (the repo slug) and/or a
category from Claude Code's own summary sections, e.g.:

- `summary.files_and_code_sections` — what files were changed and how
- `summary.errors_and_fixes` — bugs hit and how they were resolved
- `summary.pending_tasks` / `summary.current_work` — where we left off
- `summary.primary_request_and_intent` — the original goals

Prefer `memory_find` over trusting the compaction summary for older detail —
the summary may have dropped it; the store still has it.
