"""`cc-mem-init` — create the first state for a project and wire Claude Code up.

Init does three things:
  1. SCAN the repo -> a categorized ``project.*`` baseline (first state).
  2. Fold in CURRENT CONTEXT -> ingest any existing compaction summaries for
     this repo's Claude Code session(s).
  3. INSTALL guidance so Claude Code knows to query + update memory: a managed
     block in the project ``CLAUDE.md`` (always), and optionally the
     SessionStart/PostCompact hooks in ``~/.claude/settings.json`` (--install-hooks).

Idempotent: safe to re-run — scan facts dedup, the CLAUDE.md block is replaced
in place, and hooks are only added if missing.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from . import ingest, scan
from .config import Config
from .embeddings import build_embedder
from .store import MemoryStore

log = logging.getLogger("cc-mem-mcp.init")

_CLAUDE_BEGIN = "<!-- BEGIN cc-mem-mcp (managed) -->"
_CLAUDE_END = "<!-- END cc-mem-mcp (managed) -->"

_CLAUDE_BLOCK = f"""{_CLAUDE_BEGIN}
## Long-term Memory (`memory` MCP)

This project has a persistent, categorized memory that survives compaction.
Use it as the source of truth for earlier context — the in-context summary is lossy.

- **Query first.** Before re-deriving anything established earlier, call
  `memory_find(query, project="{{project}}", category?)`. Useful categories:
  `project.overview` · `project.stack` · `project.commands` · `project.connections`
  (repo facts) and `summary.files_and_code_sections` · `summary.errors_and_fixes` ·
  `summary.pending_tasks` · `summary.current_work` (session state).
- **Updates are automatic.** Compaction summaries are captured to memory by a
  hook/watcher. If you suspect a gap, call `memory_ingest(project="{{project}}")`.
- Refresh the project baseline anytime with `memory_init` (idempotent).
{_CLAUDE_END}"""


def _build_store() -> MemoryStore:
    cfg = Config.from_env()
    return MemoryStore(cfg, build_embedder(cfg))


def install_claude_md(root: Path, project: str) -> str:
    """Write/refresh the managed memory block in <root>/CLAUDE.md. Returns action."""
    block = _CLAUDE_BLOCK.replace("{project}", project)
    p = root / "CLAUDE.md"
    if not p.exists():
        p.write_text(block + "\n", encoding="utf-8")
        return "created CLAUDE.md"
    text = p.read_text(encoding="utf-8", errors="replace")
    if _CLAUDE_BEGIN in text and _CLAUDE_END in text:
        pre = text.split(_CLAUDE_BEGIN)[0].rstrip()
        post = text.split(_CLAUDE_END, 1)[1].lstrip()
        new = (pre + "\n\n" + block + "\n\n" + post).strip() + "\n"
        p.write_text(new, encoding="utf-8")
        return "updated CLAUDE.md block"
    p.write_text(text.rstrip() + "\n\n" + block + "\n", encoding="utf-8")
    return "appended CLAUDE.md block"


def install_hooks(settings_path: Optional[Path] = None) -> str:
    """Add SessionStart(cc-mem-init) + PostCompact(cc-mem-ingest) hooks if missing."""
    settings_path = settings_path or (Path.home() / ".claude" / "settings.json")
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    data: Dict[str, Any] = {}
    if settings_path.exists():
        try:
            data = json.loads(settings_path.read_text(encoding="utf-8") or "{}")
        except Exception:  # noqa: BLE001
            return "skipped hooks: settings.json is not valid JSON (edit manually)"
    hooks = data.setdefault("hooks", {})
    added = []

    def ensure(event: str, command: str) -> None:
        existing = json.dumps(hooks.get(event, []))
        if command.split()[0] in existing and command.split()[-1] in existing:
            return
        hooks.setdefault(event, []).append(
            {"matcher": "*", "hooks": [{"type": "command", "command": command, "timeout": 120}]}
        )
        added.append(event)

    ensure("PostCompact", "cc-mem-ingest --once")
    ensure("SessionStart", "cc-mem-init --no-hooks --quiet")
    if not added:
        return "hooks already present"
    settings_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return f"added hooks: {', '.join(added)} -> {settings_path}"


def run_init(root: Path, project: Optional[str] = None, claude_md: bool = True,
             hooks: bool = False, ingest_context: bool = True) -> Dict[str, Any]:
    # abspath (not resolve) so the Windows drive-letter case is preserved to
    # match Claude Code's transcript slug.
    root = Path(os.path.abspath(str(root)))
    project = project or root.name
    store = _build_store()

    facts = scan.scan_project(root, project)
    scan_result = store.add_many(facts)

    ctx = {"generations": 0, "unique": 0}
    if ingest_context:
        tdir = ingest.resolve_transcript_dir(root)
        if tdir is not None:
            totals = {"files": 0, "unique": 0, "received": 0}
            for f in ingest.find_transcripts(tdir):
                r = ingest.ingest_file(store, f, project)  # same project label as baseline
                totals["files"] += 1
                totals["unique"] += r.get("unique", 0)
            ctx = {"generations": totals["files"], "unique": totals["unique"], "slug": tdir.name}
        else:
            ctx = {"generations": 0, "unique": 0, "slug": ingest.cc_slug(root), "note": "no transcripts yet"}

    actions = []
    if claude_md:
        actions.append(install_claude_md(root, project))
    if hooks:
        actions.append(install_hooks())

    return {
        "project": project,
        "root": str(root),
        "baseline_facts": scan_result,
        "current_context": ctx,
        "setup": actions,
        "total": store.stats().get("count"),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="cc-mem-init", description=__doc__)
    p.add_argument("--root", type=Path, default=Path.cwd(), help="Repo path (default: cwd)")
    p.add_argument("--project", default=None, help="Project slug (default: repo folder name)")
    p.add_argument("--no-claude-md", action="store_true", help="Do not touch CLAUDE.md")
    p.add_argument("--install-hooks", action="store_true", help="Add SessionStart/PostCompact hooks to settings.json")
    p.add_argument("--no-hooks", action="store_true", help="Explicitly skip hooks (default)")
    p.add_argument("--no-context", action="store_true", help="Skip ingesting existing compactions")
    p.add_argument("--quiet", action="store_true", help="Less logging")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.WARNING if args.quiet else logging.INFO,
                        stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")
    result = run_init(args.root, args.project, claude_md=not args.no_claude_md,
                     hooks=args.install_hooks, ingest_context=not args.no_context)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
