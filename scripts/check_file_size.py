#!/usr/bin/env python3
"""Enforce a per-file line budget to keep modules focused and reviewable.

Run via `make lint-loc`. Fails (exit 1) if any tracked Python file exceeds the
soft cap, so a module that has grown too large gets split rather than sprawling.
"""

from __future__ import annotations

import sys
from pathlib import Path

MAX_LINES = 500
ROOTS = ("ucsc_link", "tests")
EXTRA_FILES = ("server.py", "mcp_server.py")

#: Directories (repo-relative prefixes) exempt from the budget. The MCP
#: conformance gates under ``tests/conformance/`` are vendored byte-identical from
#: the GeneFoundry router and maintained upstream -- they must not be edited here
#: to fit a local budget, so they are excluded rather than reformatted.
EXCLUDE_PREFIXES = ("tests/conformance",)


def _is_excluded(rel: Path) -> bool:
    rel_str = rel.as_posix()
    return any(rel_str.startswith(prefix) for prefix in EXCLUDE_PREFIXES)


def main() -> int:
    """Report files over the line budget; return non-zero if any are found."""
    repo = Path(__file__).resolve().parents[1]
    offenders: list[tuple[Path, int]] = []
    paths: list[Path] = [repo / f for f in EXTRA_FILES]
    for root in ROOTS:
        paths.extend((repo / root).rglob("*.py"))
    for path in paths:
        if not path.exists():
            continue
        if _is_excluded(path.relative_to(repo)):
            continue
        lines = path.read_text(encoding="utf-8").count("\n") + 1
        if lines > MAX_LINES:
            offenders.append((path.relative_to(repo), lines))
    for rel, lines in sorted(offenders):
        print(f"{rel}: {lines} lines (> {MAX_LINES})")
    if offenders:
        print(f"\n{len(offenders)} file(s) exceed the {MAX_LINES}-line budget.")
        return 1
    print(f"OK: all files within the {MAX_LINES}-line budget.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
