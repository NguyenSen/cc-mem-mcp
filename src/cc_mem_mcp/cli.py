"""`cc-mem-ingest` — capture Claude Code compaction summaries into the store.

Run once (e.g. from a PostCompact hook) or as a lightweight watcher that polls
the projects directory. Ingestion is idempotent (content-hash dedup), so the
watcher can re-scan freely.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from . import ingest
from .config import Config
from .embeddings import build_embedder
from .store import MemoryStore

log = logging.getLogger("cc-mem-mcp.cli")


def _build_store() -> MemoryStore:
    cfg = Config.from_env()
    return MemoryStore(cfg, build_embedder(cfg))


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="cc-mem-ingest", description=__doc__)
    p.add_argument("--projects-dir", type=Path, default=ingest.default_projects_dir(),
                   help="Claude Code projects dir (default: ~/.claude/projects)")
    p.add_argument("--project", default=None, help="Restrict to one project slug (transcript folder name)")
    p.add_argument("--file", type=Path, default=None, help="Ingest a single .jsonl transcript")
    p.add_argument("--once", action="store_true", help="Ingest once and exit (default; explicit for hooks)")
    p.add_argument("--watch", action="store_true", help="Keep polling for new compactions")
    p.add_argument("--interval", type=float, default=30.0, help="Watch poll interval seconds (default 30)")
    args = p.parse_args(argv)

    store = _build_store()

    def run_once() -> None:
        if args.file:
            r = ingest.ingest_file(store, args.file, args.project)
            log.info("ingested file: %s", r)
        else:
            r = ingest.ingest_dir(store, args.projects_dir, args.project)
            log.info("ingested dir: %s", r)

    run_once()
    if not args.watch:
        return 0

    log.info("watching %s every %ss (Ctrl-C to stop)", args.projects_dir, args.interval)
    try:
        while True:
            time.sleep(args.interval)
            run_once()
    except KeyboardInterrupt:
        log.info("stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
