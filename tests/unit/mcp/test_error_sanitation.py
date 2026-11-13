"""Unit contract for ``sanitize_message`` (error-message code-point fence).

Proves the primitive strips the ratified control/zero-width/bidi/NUL code points,
preserves ordinary prose, and length-caps at the fleet norm (280). The wiring of
this primitive onto the real MCP error surfaces is covered by
``test_error_leak_fencing.py``.
"""

from __future__ import annotations

from ucsc_link.mcp._sanitize import (
    MAX_MESSAGE_CHARS,
    sanitize_message,
)


def test_strips_nul_zwj_bom_and_bidi_override() -> None:
    """NUL, ZWJ, BOM, and the RTL override are removed; ordinary text survives."""
    raw = "boom\x00 zwj‍ bom﻿ bidi‮ end"
    cleaned = sanitize_message(raw)
    for forbidden in ("\x00", "‍", "﻿", "‮"):
        assert forbidden not in cleaned
    assert cleaned == "boom zwj bom bidi end"


def test_preserves_ordinary_prose_and_punctuation() -> None:
    """Plain guidance prose (incl. tab/newline-free punctuation) is unchanged."""
    msg = "UCSC rejected the request parameters."
    assert sanitize_message(msg) == msg


def test_length_capped_at_fleet_norm() -> None:
    """A message longer than the cap is truncated to MAX_MESSAGE_CHARS."""
    long_msg = "A" * (MAX_MESSAGE_CHARS + 50)
    assert len(sanitize_message(long_msg)) == MAX_MESSAGE_CHARS
    assert MAX_MESSAGE_CHARS == 280
