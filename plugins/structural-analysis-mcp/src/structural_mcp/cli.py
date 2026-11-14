"""Entry points: one stdio MCP server per program."""

from __future__ import annotations

import argparse
import importlib
import logging
import sys

ADAPTERS = {
    "sap2000": ("structural_mcp.sap2000.adapter", "Sap2000Adapter"),
    "staad": ("structural_mcp.staad.adapter", "StaadAdapter"),
    "lira": ("structural_mcp.lira.adapter", "LiraAdapter"),
    "sapfir": ("structural_mcp.sapfir.adapter", "SapfirAdapter"),
}


def make_adapter(program: str):
    module, cls = ADAPTERS[program]
    return getattr(importlib.import_module(module), cls)()


def main(program: str | None = None) -> None:
    parser = argparse.ArgumentParser(description="Structural analysis MCP server (stdio).")
    if program is None:
        parser.add_argument("program", choices=sorted(ADAPTERS))
    parser.add_argument("--log-level", default="WARNING")
    args = parser.parse_args()
    # stdout carries the MCP protocol; logs go to stderr only.
    logging.basicConfig(level=args.log_level.upper(), stream=sys.stderr)
    from .core.server import build_server

    build_server(make_adapter(program or args.program)).run("stdio")


def sap2000() -> None:
    main("sap2000")


def staad() -> None:
    main("staad")


def lira() -> None:
    main("lira")


def sapfir() -> None:
    main("sapfir")


if __name__ == "__main__":
    main()
