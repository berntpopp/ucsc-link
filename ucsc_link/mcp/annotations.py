"""Shared tool annotations: every ucsc-link tool is a read-only query of UCSC."""

from __future__ import annotations

from mcp.types import ToolAnnotations

READ_ONLY_OPEN_WORLD = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
