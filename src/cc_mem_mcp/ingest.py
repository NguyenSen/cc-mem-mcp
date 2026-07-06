"""Capture Claude Code's own compaction summaries from the on-disk transcript.

Claude Code writes each session to ``~/.claude/projects/<slug>/<session>.jsonl``.
When the context is compacted it appends a line with ``isCompactSummary: true``
whose message is the freshly re-summarised, ALREADY-CATEGORISED state — Claude
Code's default template numbers the categories:

    1. Primary Request and Intent      6. All user messages
    2. Key Technical Concepts          7. Pending Tasks
    3. Files and Code Sections         8. Current Work
    4. Errors and fixes                9. Optional Next Step
    5. Problem Solving

We do NOT impose our own taxonomy — we take those section titles as the
(free-form) categories. Each section is split into paragraph/bullet chunks and
upserted with content-hash dedup, so a chunk that survives unchanged across N
compactions is stored once, while a detail dropped by a later compaction is
still retained from the generation that had it. That is what makes the capture
lossless across compaction generations.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Iterator, List, Optional, Tuple

if TYPE_CHECKING:  # avoid importing qdrant just to parse transcripts
    from .store import MemoryStore

log = logging.getLogger("cc-mem-mcp.ingest")

# "1. Primary Request and Intent:"  ->  ("1", "Primary Request and Intent")
_SECTION_RE = re.compile(r"^\s*(\d+)\.\s+([A-Z][^:\n]{2,60}):\s*$")


def default_projects_dir() -> Path:
    return Path(os.getenv("CLAUDE_PROJECTS_DIR") or (Path.home() / ".claude" / "projects"))


def cc_slug(repo_path: Path) -> str:
    """Derive Claude Code's transcript folder name from a repo path.

    Claude Code turns the absolute cwd into a folder under ~/.claude/projects by
    replacing every non-alphanumeric character with '-'. E.g.
    ``d:\\work\\my-repo`` -> ``d--work-my-repo``.

    Uses ``os.path.abspath`` (not ``Path.resolve``) so the Windows drive-letter
    case is preserved to match Claude Code's own slug.
    """
    abspath = os.path.abspath(str(repo_path))
    return "".join(c if c.isalnum() else "-" for c in abspath)


def resolve_transcript_dir(repo_path: Path, projects_dir: Optional[Path] = None) -> Optional[Path]:
    """Locate the transcript folder for a repo, tolerant of drive-letter case."""
    projects_dir = projects_dir or default_projects_dir()
    slug = cc_slug(repo_path)
    exact = projects_dir / slug
    if exact.exists():
        return exact
    if projects_dir.exists():
        low = slug.lower()
        for d in projects_dir.iterdir():
            if d.is_dir() and d.name.lower() == low:
                return d
    return None


def slugify_category(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", title.strip().lower()).strip("_")
    return f"summary.{s}" if s else "summary.uncategorized"


def _message_text(entry: Dict[str, Any]) -> str:
    msg = entry.get("message") or {}
    c = msg.get("content")
    if isinstance(c, list):
        return "".join(b.get("text", "") for b in c if isinstance(b, dict))
    return c if isinstance(c, str) else ""


def iter_compactions(path: Path) -> Iterator[Tuple[int, Dict[str, Any], str]]:
    """Yield ``(generation, entry, text)`` for each compaction summary in a file."""
    gen = 0
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or '"isCompactSummary"' not in line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not entry.get("isCompactSummary"):
                continue
            gen += 1
            yield gen, entry, _message_text(entry)


def parse_sections(text: str) -> List[Tuple[str, str]]:
    """Split a summary into ``(category, body)`` by its numbered section headers.

    If the template can't be recognised (custom CLAUDE.md guidance, older format),
    fall back to a single ``summary.full`` section so nothing is lost.
    """
    lines = text.splitlines()
    sections: List[Tuple[str, List[str]]] = []
    current: Optional[str] = None
    for ln in lines:
        m = _SECTION_RE.match(ln)
        if m:
            current = slugify_category(m.group(2))
            sections.append((current, []))
        elif sections:
            sections[-1][1].append(ln)
    if not sections:
        return [("summary.full", text.strip())] if text.strip() else []
    return [(cat, "\n".join(body).strip()) for cat, body in sections if "\n".join(body).strip()]


def chunk_body(body: str, max_chars: int = 700) -> List[str]:
    """Break a section body into bullet/paragraph chunks for stable dedup.

    Bullets (``- ``/``* ``/``N. ``) start new chunks; overly long paragraphs are
    split on blank lines then hard-wrapped so a single huge blob doesn't dominate.
    """
    chunks: List[str] = []
    buf: List[str] = []

    def flush() -> None:
        if buf:
            text = "\n".join(buf).strip()
            if text:
                chunks.append(text)
            buf.clear()

    for para in re.split(r"\n\s*\n", body):
        para = para.strip()
        if not para:
            continue
        for ln in para.splitlines():
            if re.match(r"^\s*(?:[-*]|\d+\.)\s+", ln) and buf:
                flush()
            buf.append(ln)
        flush()

    # hard-wrap any oversized chunk
    out: List[str] = []
    for ch in chunks:
        if len(ch) <= max_chars:
            out.append(ch)
        else:
            for i in range(0, len(ch), max_chars):
                out.append(ch[i : i + max_chars])
    return out


def build_items(path: Path, project: str) -> List[Dict[str, Any]]:
    """Turn every compaction in a transcript into dedup-ready store items."""
    items: List[Dict[str, Any]] = []
    for gen, entry, text in iter_compactions(path):
        ts = entry.get("timestamp")
        session = entry.get("sessionId") or path.stem
        for category, body in parse_sections(text):
            for chunk in chunk_body(body):
                items.append(
                    {
                        "content": chunk,
                        "category": category,
                        "project": project,
                        "generation": gen,
                        "ts": ts,
                        "source": f"compaction:{session}#{gen}",
                        "tags": ["compaction", session],
                    }
                )
    return items


def project_for(path: Path) -> str:
    """Derive a project slug from the transcript's parent directory name."""
    return path.parent.name or path.stem


def ingest_file(store: "MemoryStore", path: Path, project: Optional[str] = None) -> Dict[str, Any]:
    proj = project or project_for(path)
    items = build_items(path, proj)
    result = store.add_many(items)
    result.update({"file": str(path), "project": proj})
    log.info("ingested %s: %s", path.name, result)
    return result


def find_transcripts(projects_dir: Path, project: Optional[str] = None) -> List[Path]:
    root = projects_dir if project is None else (projects_dir / project)
    if not root.exists():
        return []
    # top-level session files only — skip subagent/workflow sidechains
    return [p for p in root.glob("*.jsonl") if p.is_file()]


def ingest_dir(store: "MemoryStore", projects_dir: Path, project: Optional[str] = None) -> Dict[str, Any]:
    files = find_transcripts(projects_dir, project)
    totals = {"files": 0, "unique": 0, "received": 0}
    for f in files:
        r = ingest_file(store, f)
        totals["files"] += 1
        totals["unique"] += r.get("unique", 0)
        totals["received"] += r.get("received", 0)
    return totals
