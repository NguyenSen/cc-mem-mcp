#!/usr/bin/env python
"""Append a gold case to a gold JSONL file (validated, de-duplicated).

Use it the moment you notice an important fact was captured, so your regression
set grows with the memory:

    python -m eval.add_gold "which SSH port reaches the server" 8686
    python -m eval.add_gold "why multilingual model" multilingual --file eval/gold.server-deploy.jsonl

By default the second arg is an ``expect_substring``. Pass ``--id`` to match a
specific point id instead.
"""

from __future__ import annotations

import argparse
import json
import os
import sys


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Append a validated gold case.")
    ap.add_argument("query")
    ap.add_argument("expect", help="substring expected in a top-k hit (or a point id with --id)")
    ap.add_argument("--id", action="store_true", help="treat EXPECT as expect_id, not a substring")
    ap.add_argument("--category", default=None)
    ap.add_argument("--project", default=None)
    ap.add_argument("--file", default="eval/gold.server-deploy.jsonl")
    args = ap.parse_args(argv)

    case = {"query": args.query}
    case["expect_id" if args.id else "expect_substring"] = args.expect
    if args.category:
        case["category"] = args.category
    if args.project:
        case["project"] = args.project

    # skip if an identical query already exists
    if os.path.exists(args.file):
        with open(args.file, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("//"):
                    continue
                try:
                    if json.loads(line).get("query") == args.query:
                        print(f"already present: {args.query!r} — skipped")
                        return 0
                except json.JSONDecodeError:
                    pass

    with open(args.file, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(case, ensure_ascii=False) + "\n")
    print(f"added -> {args.file}: {json.dumps(case, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
