# Changelog

All notable changes to this project are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/).

## [0.1.0] - Unreleased

Initial release.

### Added
- MCP server exposing `memory_init`, `memory_ingest`, `memory_find`,
  `memory_store`, `memory_categories`, `memory_delete`, `memory_stats`.
- Transcript ingester that captures Claude Code's own compaction summaries from
  `~/.claude/projects/<slug>/*.jsonl`, parsing their numbered sections as
  free-form categories (`summary.*`).
- Content-hash dedup with existence check, so recurring ingest only embeds the
  delta — lossless across compaction generations without blow-up.
- Project scanner (`cc-mem-init`) producing a `project.*` baseline (overview,
  stack, structure, commands, connections, git, docs) and a managed
  `## Long-term Memory` block in `CLAUDE.md`.
- Qdrant backend: embedded local-file by default, or a shared server via
  `QDRANT_URL`.
- Embeddings: local FastEmbed (default, offline) or OpenAI-compatible.
- Docker image, `docker-compose` (shared Qdrant + optional watcher), and a
  GitHub Actions workflow to publish the image to GHCR.
- PostCompact hook templates (Windows `.cmd`, Unix `.sh`) for automatic capture.
