"""README Standard v1 guard -- the '## Tools' table must match the registered tool set.

The README's tool table is the server's advertised surface. Hand-maintained, it rots the
moment a tool is added or renamed. This test makes it machine-verified: the table's tool
names must equal the live registered tools EXACTLY (as sets).

The live tool list is obtained the same way ``tests/unit/test_tool_names.py`` does --
``create_ucsc_mcp().list_tools()`` -- so there is one source of truth, never a
hardcoded list to drift alongside the README.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ucsc_link.mcp.facade import create_ucsc_mcp

pytestmark = pytest.mark.mcp

README = Path(__file__).resolve().parents[2] / "README.md"

#: A table row's first cell, e.g. ``| `resolve_transcript` | Resolve ... |``.
_TOOL_ROW_RE = re.compile(r"^\|\s*`([a-z0-9_]+)`\s*\|")


async def _registered_tools() -> list:
    """Live tool objects from the facade (no service needed for listing)."""
    return await create_ucsc_mcp().list_tools()


def _readme_tool_table() -> list[str]:
    """Tool names listed in the README's '## Tools' table, in document order."""
    lines = README.read_text(encoding="utf-8").splitlines()

    try:
        start = next(i for i, ln in enumerate(lines) if ln.strip() == "## Tools")
    except StopIteration:  # pragma: no cover - guarded by the assertion below
        return []

    names: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("## "):  # next section ends the table
            break
        match = _TOOL_ROW_RE.match(line)
        if match:
            names.append(match.group(1))
    return names


def test_readme_has_a_tools_table() -> None:
    """Fail loudly rather than vacuously passing on an empty/renamed section."""
    assert README.exists(), f"README.md not found at {README}"
    assert _readme_tool_table(), "no tool rows parsed from the README '## Tools' table"


async def test_readme_tools_table_matches_registered_tools() -> None:
    """The README table and the live tool set must agree exactly."""
    documented = _readme_tool_table()
    registered = {t.name for t in await _registered_tools()}

    missing = registered - set(documented)
    extra = set(documented) - registered

    assert not missing, (
        f"tools registered but absent from the README '## Tools' table: {sorted(missing)}. "
        f"Add a row for each (README Standard v1, Rule 6)."
    )
    assert not extra, (
        f"tools listed in the README '## Tools' table but not registered: {sorted(extra)}. "
        f"Remove the stale rows (README Standard v1, Rule 6)."
    )
    assert set(documented) == registered


def test_readme_tool_table_has_no_duplicate_rows() -> None:
    """A duplicated row would still pass a set comparison -- catch it explicitly."""
    documented = _readme_tool_table()
    duplicates = sorted({name for name in documented if documented.count(name) > 1})
    assert not duplicates, f"duplicate rows in the README '## Tools' table: {duplicates}"
