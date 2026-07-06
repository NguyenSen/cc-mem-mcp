"""MCP server exposing categorized write-through memory tools.

Design: state does not live in the conversation/compaction summary — it is
written through to Qdrant the moment a durable fact appears, and retrieved on
demand. Compaction can then drop detail harmlessly because the source of truth
is external.

Tools:
    memory_store       persist one categorized fact (call this continuously)
    memory_find        semantic retrieval, filterable by category/project
    memory_categories  list the active taxonomy
    memory_delete      remove a fact by id
    memory_stats       collection + backend info
"""

from __future__ import annotations

import logging
import sys
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP

from . import categories as cat
from .config import Config
from .embeddings import build_embedder
from .store import MemoryStore

# MCP speaks JSON-RPC over stdout; ALL logging must go to stderr.
logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("cc-mem-mcp")

mcp = FastMCP("cc-mem")

_store: Optional[MemoryStore] = None


def get_store() -> MemoryStore:
    """Build the store lazily so heavy imports (fastembed) load only once."""
    global _store
    if _store is None:
        cfg = Config.from_env()
        _store = MemoryStore(cfg, build_embedder(cfg))
    return _store


@mcp.tool()
def memory_store(
    content: str,
    category: str,
    project: Optional[str] = None,
    tags: Optional[List[str]] = None,
    source: Optional[str] = None,
) -> Dict[str, Any]:
    """Persist ONE durable fact to long-term memory (write-through).

    Call this the moment a fact worth surviving compaction appears — do NOT wait
    for the conversation to be summarized. One call = one atomic fact.

    category is ``domain.sub``. Built-in taxonomy:
      code.rules        conventions, constraints, do/don't agreed this session
      code.workflow     current procedure/steps, what's done, what's pending
      code.os           OS, shell, tool versions, paths, env vars
      code.connections  hosts / SSH / ports / domains / services / DBs in use
      code.files        files changed, with absolute paths
      code.issues       unresolved bugs / blockers
      business.goal     the business problem being solved, expected outcome
      business.decision business decisions + rationale
      business.constraint requirements, limits, deadlines, stakeholders
      business.state    where we are in the business flow

    project: optional slug to scope the fact to one project/repo.
    tags: optional keywords for later filtering.
    source: optional origin note (e.g. a file path or URL).
    """
    return get_store().store(content=content, category=category, project=project, tags=tags, source=source)


@mcp.tool()
def memory_find(
    query: str,
    category: Optional[str] = None,
    project: Optional[str] = None,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """Retrieve relevant facts by meaning (semantic search).

    Call this at the start of a task instead of relying on the compaction
    summary. Narrow with category (``code.connections``, or a whole domain like
    ``code``) and/or project. Returns the top matches with a similarity score.
    """
    return get_store().find(query=query, category=category, project=project, limit=limit)


@mcp.tool()
def memory_categories() -> Dict[str, Any]:
    """List the active category taxonomy (domains and their sub-categories)."""
    return {"taxonomy": cat.load_taxonomy(), "flat": cat.flat_list(), "strict": cat.strict()}


@mcp.tool()
def memory_delete(id: str) -> Dict[str, Any]:
    """Delete a stored fact by its id (as returned by memory_store/memory_find)."""
    return get_store().delete(id)


@mcp.tool()
def memory_init(root: Optional[str] = None, project: Optional[str] = None,
                install_claude_md: bool = True, install_hooks: bool = False) -> Dict[str, Any]:
    """Create the FIRST STATE for a project and wire Claude Code to use memory.

    Call this once at the start of working on a repo (or to refresh — it's
    idempotent). It:
      1. Scans the repo into a categorized ``project.*`` baseline (overview, stack,
         structure, commands, connections, git, docs).
      2. Folds in current context: ingests any existing compaction summaries for
         this repo's Claude Code session(s).
      3. Installs a managed memory block in the project CLAUDE.md so the agent
         knows to query (memory_find) and rely on automatic updates.

    root: repo path (defaults to the server's working directory). When running in
          Docker, mount the repo and pass its in-container path here.
    install_hooks: also add SessionStart/PostCompact hooks to ~/.claude/settings.json.
    """
    from pathlib import Path

    from . import init as _init

    return _init.run_init(
        Path(root) if root else Path.cwd(),
        project=project,
        claude_md=install_claude_md,
        hooks=install_hooks,
    )


@mcp.tool()
def memory_ingest(project: Optional[str] = None, session_path: Optional[str] = None) -> Dict[str, Any]:
    """Capture Claude Code's OWN compaction summaries from disk into memory.

    This is the primary write path: instead of tagging facts by hand, it reads
    the transcript(s) Claude Code writes to ``~/.claude/projects/<slug>/*.jsonl``,
    finds every compaction summary, and stores each of the summary's numbered
    sections (Primary Request, Files and Code Sections, Errors and fixes, Pending
    Tasks, ...) as categorized, dedup'd chunks. Safe to run repeatedly — unchanged
    chunks re-map to the same id, so nothing piles up and nothing is lost across
    compaction generations.

    project: restrict to one project slug (the transcript folder name). Omit to
             scan all projects.
    session_path: ingest a single .jsonl transcript instead of scanning.
    """
    from pathlib import Path

    from . import ingest

    store = get_store()
    if session_path:
        return ingest.ingest_file(store, Path(session_path), project)
    return ingest.ingest_dir(store, ingest.default_projects_dir(), project)


@mcp.tool()
def memory_stats() -> Dict[str, Any]:
    """Report collection size, storage backend, and embedding configuration."""
    return get_store().stats()


def run() -> None:
    mcp.run()
