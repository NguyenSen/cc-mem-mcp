"""Lightweight project scanner -> categorized baseline ("first state").

Produces ``project.*`` facts about a repo without reading every file (deep code
search is the job of a code-graph tool, not this). Everything is best-effort and
wrapped in try/except so a missing git binary or odd repo never breaks init.

Categories emitted:
    project.overview      name, description, git branch/remotes
    project.stack         languages/frameworks from manifests
    project.structure     top-level layout
    project.commands      build/test/run commands
    project.connections   hosts/services/ports from compose/.env/docs
    project.git           recent commits
    project.docs          README / CLAUDE.md excerpts
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("cc-mem-mcp.scan")

_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build",
             ".next", ".cache", "target", ".idea", ".vscode", "vendor"}
_MANIFESTS = {
    "package.json": "Node.js/JavaScript",
    "pyproject.toml": "Python",
    "requirements.txt": "Python",
    "go.mod": "Go",
    "Cargo.toml": "Rust",
    "pom.xml": "Java/Maven",
    "build.gradle": "Java/Gradle",
    "Gemfile": "Ruby",
    "composer.json": "PHP",
    "docker-compose.yml": "Docker Compose",
    "docker-compose.yaml": "Docker Compose",
    "Dockerfile": "Docker",
    "Makefile": "Make",
}


def _git(root: Path, *args: str) -> Optional[str]:
    try:
        out = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                             text=True, timeout=15, encoding="utf-8", errors="replace")
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception as exc:  # noqa: BLE001
        log.debug("git %s failed: %s", args, exc)
    return None


def _read(path: Path, limit: int = 4000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except Exception:  # noqa: BLE001
        return ""


def _first_paragraph(md: str) -> str:
    lines = []
    for ln in md.splitlines():
        s = ln.strip()
        if s.startswith("#") or not s:
            if lines:
                break
            continue
        lines.append(s)
        if len(lines) >= 6:
            break
    return " ".join(lines)


def scan_project(root: Path, project: str) -> List[Dict[str, Any]]:
    root = root.resolve()
    facts: List[Dict[str, Any]] = []

    def add(category: str, content: str) -> None:
        content = (content or "").strip()
        if content:
            facts.append({"content": content, "category": category, "project": project,
                          "source": "init:scan", "generation": 0})

    # --- overview + git ----------------------------------------------------
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    remotes = _git(root, "remote", "-v")
    overview = [f"Project '{project}' at {root}"]
    if branch:
        overview.append(f"git branch: {branch}")
    if remotes:
        first_remote = remotes.splitlines()[0] if remotes else ""
        overview.append(f"git remote: {first_remote}")
    add("project.overview", "\n".join(overview))

    log_out = _git(root, "log", "--oneline", "-n", "15")
    if log_out:
        add("project.git", "Recent commits:\n" + log_out)

    # --- docs --------------------------------------------------------------
    for name in ("README.md", "README", "CLAUDE.md"):
        p = root / name
        if p.exists():
            txt = _read(p)
            if name.upper().startswith("README"):
                add("project.overview", f"{name} intro: {_first_paragraph(txt)}")
            add("project.docs", f"{name}:\n{txt[:1500]}")

    # --- stack + commands + connections from manifests ---------------------
    langs: List[str] = []
    for fname, label in _MANIFESTS.items():
        p = root / fname
        if not p.exists():
            continue
        langs.append(label)
        if fname == "package.json":
            try:
                data = json.loads(_read(p, 8000) or "{}")
                if data.get("scripts"):
                    cmds = "\n".join(f"  npm run {k}: {v}" for k, v in list(data["scripts"].items())[:20])
                    add("project.commands", f"package.json scripts:\n{cmds}")
                deps = list((data.get("dependencies") or {}).keys())[:25]
                if deps:
                    add("project.stack", "npm dependencies: " + ", ".join(deps))
            except Exception:  # noqa: BLE001
                pass
        elif fname == "pyproject.toml":
            txt = _read(p, 8000)
            add("project.stack", f"pyproject.toml (excerpt):\n{txt[:800]}")
            scripts = re.findall(r"^\s*([\w-]+)\s*=\s*\"[\w.:]+\"", txt, re.M)
            if "[project.scripts]" in txt and scripts:
                add("project.commands", "console scripts: " + ", ".join(scripts[:15]))
        elif fname in ("docker-compose.yml", "docker-compose.yaml"):
            txt = _read(p, 6000)
            services = re.findall(r"^\s{2}([a-zA-Z0-9_-]+):\s*$", txt, re.M)
            ports = re.findall(r"\"?(\d{2,5}):\d{2,5}\"?", txt)
            add("project.connections",
                f"docker-compose services: {', '.join(services[:20])}"
                + (f"\nexposed ports: {', '.join(sorted(set(ports))[:20])}" if ports else ""))
        elif fname == "Makefile":
            txt = _read(p, 6000)
            targets = re.findall(r"^([a-zA-Z0-9_-]+):", txt, re.M)
            if targets:
                add("project.commands", "make targets: " + ", ".join(dict.fromkeys(targets))[:400])

    if langs:
        add("project.stack", "detected: " + ", ".join(dict.fromkeys(langs)))

    # .env.example -> connection/config KEYS only (never values)
    for envname in (".env.example", ".env.sample", ".env.template"):
        p = root / envname
        if p.exists():
            keys = re.findall(r"^\s*([A-Z][A-Z0-9_]+)=", _read(p, 4000), re.M)
            if keys:
                add("project.connections", f"{envname} config keys: " + ", ".join(dict.fromkeys(keys))[:600])

    # --- structure ---------------------------------------------------------
    try:
        top = []
        for entry in sorted(root.iterdir(), key=lambda e: (e.is_file(), e.name.lower())):
            if entry.name in _SKIP_DIRS or entry.name.startswith("."):
                continue
            top.append(entry.name + ("/" if entry.is_dir() else ""))
        if top:
            add("project.structure", "top-level: " + ", ".join(top[:40]))
    except Exception:  # noqa: BLE001
        pass

    return facts
