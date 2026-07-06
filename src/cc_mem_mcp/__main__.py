"""Entry point: `cc-mem-mcp` runs the MCP server over stdio."""

from __future__ import annotations


def main() -> None:
    from .server import run

    run()


if __name__ == "__main__":
    main()
