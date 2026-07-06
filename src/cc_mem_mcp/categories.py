"""Category taxonomy for stored memories.

A category is a ``domain.sub`` string, e.g. ``code.connections`` or
``business.decision``. The default taxonomy mirrors the two buckets the
tool was designed around:

    CODE      -> rules, workflow, os, connections, files, issues
    BUSINESS  -> goal, decision, constraint, state

Override at runtime with the ``CC_MEM_CATEGORIES`` env var (JSON), and make
unknown categories a hard error with ``CC_MEM_STRICT_CATEGORIES=1``.
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Tuple

DEFAULT_TAXONOMY: Dict[str, List[str]] = {
    "code": ["rules", "workflow", "os", "connections", "files", "issues"],
    "business": ["goal", "decision", "constraint", "state"],
}


def load_taxonomy() -> Dict[str, List[str]]:
    raw = os.getenv("CC_MEM_CATEGORIES")
    if not raw:
        return {k: list(v) for k, v in DEFAULT_TAXONOMY.items()}
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("CC_MEM_CATEGORIES must be a JSON object")
        return {str(k): [str(x) for x in v] for k, v in parsed.items()}
    except Exception as exc:  # noqa: BLE001 - surface config error clearly
        raise ValueError(f"Invalid CC_MEM_CATEGORIES: {exc}") from exc


def split_category(category: str) -> Tuple[str, str]:
    """Return ``(domain, sub)`` for a category string.

    Accepts:
      * ``code.connections`` -> ``("code", "connections")``
      * ``code``             -> ``("code", "")``   (whole domain, for filters)
      * ``connections``      -> ``("code", "connections")`` if the sub is
                                 unambiguous across the taxonomy
    Domain/sub are lowercased and trimmed.
    """
    cat = (category or "").strip().lower().replace("/", ".").replace(":", ".")
    if not cat:
        return ("", "")
    if "." in cat:
        domain, sub = cat.split(".", 1)
        return (domain.strip(), sub.strip())
    taxonomy = load_taxonomy()
    # bare token that names a domain -> whole-domain selector
    if cat in taxonomy:
        return (cat, "")
    # bare sub -> infer the domain if it is unambiguous
    hits = [d for d, subs in taxonomy.items() if cat in subs]
    if len(hits) == 1:
        return (hits[0], cat)
    return ("", cat)


def normalize_category(category: str) -> str:
    domain, sub = split_category(category)
    if domain and sub:
        return f"{domain}.{sub}"
    return domain or sub


def is_known(category: str) -> bool:
    domain, sub = split_category(category)
    taxonomy = load_taxonomy()
    return domain in taxonomy and sub in taxonomy[domain]


def strict() -> bool:
    return os.getenv("CC_MEM_STRICT_CATEGORIES", "0").strip() in ("1", "true", "yes")


def flat_list() -> List[str]:
    """All valid ``domain.sub`` categories as a flat list."""
    out: List[str] = []
    for domain, subs in load_taxonomy().items():
        out.extend(f"{domain}.{sub}" for sub in subs)
    return out
