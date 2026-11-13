"""ucsc-link: an MCP/API server with read-only data tools and one explicit build trigger."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("ucsc-link")
except PackageNotFoundError:  # pragma: no cover - source checkout without install
    __version__ = "0.0.0"

__all__ = ["__version__"]
