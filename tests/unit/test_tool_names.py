"""Tool-Naming Standard v1.1 guard -- every registered MCP tool must be fleet-compliant.

Backfills the Rule 8 / Definition-of-Done lint guard that was missing for this
repo. The contract mirrors genefoundry-router's ``cli.check_leaf_name`` so the
gateway and every ``-link`` leaf agree on what a compliant tool name is.

Adopts the ratified two-tier verb canon (2026-06-30):
  Tier-1 -- universal read/query: get, search, list, resolve, find, compare, compute, map
  Tier-2 -- sanctioned domain action/compute: predict, annotate, recode, liftover, analyze,
            score, submit, export, generate, download
  ops/meta carve-out -- tools tagged 'ops' or 'meta' skip the verb rule (charset/length
  and no-self-prefix still apply). See docs/TOOL-NAMING-STANDARD-v1.md Q3.

No per-tool exemptions: every ucsc-link tool name uses a Tier-1 verb.
"""

from __future__ import annotations

import re

import pytest

from ucsc_link.mcp.capabilities import TOOLS
from ucsc_link.mcp.facade import create_ucsc_mcp

pytestmark = pytest.mark.mcp

# --- Tool-Naming Standard v1.1 contract ---
_LEAF_NAME_RE = re.compile(r"^[a-z0-9_]{1,50}$")

#: Tier-1 -- universal read/query canon (Standard v1.1).
_TIER1_VERBS = frozenset({"get", "search", "list", "resolve", "find", "compare", "compute", "map"})

#: Tier-2 -- sanctioned domain action/compute verbs (fleet-wide, Standard v1.1).
_TIER2_VERBS = frozenset(
    {
        "predict",
        "annotate",
        "recode",
        "liftover",
        "analyze",
        "score",
        "submit",
        "export",
        "generate",
        "download",
    }
)

#: Union of all canonical verbs (Tier-1 + Tier-2).
_CANONICAL_VERBS = _TIER1_VERBS | _TIER2_VERBS

#: Tags that exempt a tool from the verb rule (Standard v1.1 Q3 ops/meta carve-out).
_OPS_META_TAGS = frozenset({"ops", "meta"})

_UCSC_VERB_EXEMPT: frozenset[str] = frozenset()

_NAMESPACE = "ucsc"


async def _registered_tools() -> list:
    """Live tool objects (name + tags) from the facade (no service needed for listing)."""
    return await create_ucsc_mcp().list_tools()


async def test_every_tool_name_is_standard_v1_1_compliant() -> None:
    """Every tool must pass charset/length, no-self-prefix, and verb canon.

    ops/meta-tagged tools are exempt from the verb rule; tools in
    _UCSC_VERB_EXEMPT are temporarily exempt pending a follow-up rename.
    """
    tools = await _registered_tools()
    assert tools, "no tools registered on the facade"
    violations: dict[str, list[str]] = {}
    for t in tools:
        issues: list[str] = []
        if not _LEAF_NAME_RE.match(t.name):
            issues.append(f"charset/length: {t.name!r} must match ^[a-z0-9_]{{1,50}}$ (<=50)")
        if t.name.startswith(f"{_NAMESPACE}_"):
            issues.append(f"self-prefix: {t.name!r} must not start with '{_NAMESPACE}_'")
        tags = frozenset(getattr(t, "tags", None) or ())
        # Verb check: skip for ops/meta-tagged tools and per-tool exempt set.
        if not (tags & _OPS_META_TAGS) and t.name not in _UCSC_VERB_EXEMPT:
            verb = t.name.split("_", 1)[0]
            if verb not in _CANONICAL_VERBS:
                issues.append(
                    f"verb: {t.name!r} starts with non-canonical verb {verb!r}; "
                    f"expected one of {sorted(_CANONICAL_VERBS)}"
                )
        if issues:
            violations[t.name] = issues
    assert not violations, f"Tool-Naming Standard v1.1 violations: {violations}"


async def test_exempt_tools_still_pass_charset_and_length() -> None:
    """Exempt tools skip the verb rule but must still have valid shape."""
    tools = {t.name: t for t in await _registered_tools()}
    for name in _UCSC_VERB_EXEMPT:
        assert name in tools, f"exempt tool {name!r} not registered -- update the exempt set"
        assert _LEAF_NAME_RE.match(name), f"exempt tool {name!r} violates charset/length rule"
        assert not name.startswith(f"{_NAMESPACE}_"), f"exempt tool {name!r} must not self-prefix"


async def test_live_tools_match_capabilities_tools() -> None:
    live = {t.name for t in await _registered_tools()}
    assert live == set(TOOLS), f"capabilities.TOOLS drift: {live ^ set(TOOLS)}"


async def test_every_tool_has_a_domain_tag() -> None:
    tools = await _registered_tools()
    untagged = [t.name for t in tools if not getattr(t, "tags", None)]
    assert not untagged, f"tools missing a domain tag (Rule 6): {untagged}"
