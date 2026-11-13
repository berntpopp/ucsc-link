"""Process-wide dependency-injection registry for the UCSC service.

The running server registers a concrete :class:`UcscService` at startup; tests inject
one over a respx-mocked client. An unregistered service is an ``internal`` error.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ucsc_link.exceptions import InternalError

if TYPE_CHECKING:  # pragma: no cover - import only for type checkers
    from ucsc_link.service import UcscService

_service: UcscService | None = None


def get_ucsc_service() -> UcscService:
    """Return the registered :class:`UcscService` or raise ``InternalError``."""
    if _service is None:
        raise InternalError(
            "UCSC service is not initialised; the server must register it at startup.",
            recovery_action="switch_tool",
        )
    return _service


def set_ucsc_service(service: UcscService | None) -> None:
    """Register (or clear) the process-wide service. Used by the server and tests."""
    global _service
    _service = service
