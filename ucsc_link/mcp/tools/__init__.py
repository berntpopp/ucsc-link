"""UCSC MCP tool registrations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ucsc_link.mcp.tools.blat import register_blat_tools
from ucsc_link.mcp.tools.discovery import register_discovery_tools
from ucsc_link.mcp.tools.region import register_region_tools

if TYPE_CHECKING:
    from fastmcp import FastMCP


def register_all_tools(mcp: FastMCP) -> None:
    """Register every tool group on a FastMCP instance in canonical order."""
    register_discovery_tools(mcp)
    register_region_tools(mcp)
    register_blat_tools(mcp)
