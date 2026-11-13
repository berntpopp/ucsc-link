"""Builders for ``_meta.next_commands`` entries: ready-to-call ``{tool, arguments}`` steps."""

from __future__ import annotations

from typing import Any

from ucsc_link.tracks import MAX_FEATURE_WINDOW_BP, MAX_SEQUENCE_BP, TRACKS


def cmd(tool: str, **arguments: Any) -> dict[str, Any]:
    """One ready-to-call next step."""
    return {"tool": tool, "arguments": arguments}


def default_error_next_commands(
    tool: str, error_code: str, arguments: dict[str, Any]
) -> list[dict[str, Any]]:
    """A sensible recovery step for any error lacking an explicit fallback."""
    if error_code in ("upstream_unavailable", "rate_limited"):
        return [cmd("get_diagnostics")]
    start, end = arguments.get("start"), arguments.get("end")
    if error_code == "invalid_input" and isinstance(start, int) and isinstance(end, int):
        spec = TRACKS.get(str(arguments.get("track")))
        widest = MAX_SEQUENCE_BP if tool == "get_reference_sequence" else MAX_FEATURE_WINDOW_BP
        widest = spec.max_window_bp if spec is not None else widest
        if start >= 0 and end - start > widest:  # oversized window: offer the first slice
            return [cmd(tool, **{**arguments, "end": start + widest})]
    if tool in ("get_region_annotations", "list_tracks"):
        assembly = arguments.get("assembly")
        return [cmd("list_tracks", assembly=assembly) if assembly else cmd("list_tracks")]
    return [cmd("get_server_capabilities")]
