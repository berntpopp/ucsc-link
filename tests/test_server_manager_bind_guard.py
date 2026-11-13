"""F-04 gate: the fail-closed bind guard must be enforced at the server-manager
bind sites, not only at the ``server.py`` entry point.

``check_bind_safety`` was previously called only in ``server.py`` before
dispatching to the manager. Any code path that calls
:meth:`UnifiedServerManager.start_unified_server` /
:meth:`start_http_only_server` directly therefore bypassed the guard and could
bind a non-loopback interface without the ``UCSC_LINK_ALLOW_PUBLIC_BIND``
opt-in. These tests pin the guard at the actual bind sites: a refusal happens
BEFORE uvicorn ever serves, and the opt-in path warns loudly and proceeds.
"""

from __future__ import annotations

import pytest

from ucsc_link import server_manager
from ucsc_link.config import InsecureBindError, settings

START_METHODS = ["start_unified_server", "start_http_only_server"]


class _RecordingLogger:
    """Minimal logger stub that records warnings; ignores info calls."""

    def __init__(self) -> None:
        self.warnings: list[str] = []

    def info(self, *args: object, **kwargs: object) -> None:
        pass

    def warning(self, event: str, *args: object, **kwargs: object) -> None:
        self.warnings.append(event)


class _SpyUvicornServer:
    """Fake uvicorn.Server recording whether serve() (the actual bind) ran."""

    served = False

    def __init__(self, _config: object) -> None:
        self.should_exit = False

    async def serve(self) -> None:
        _SpyUvicornServer.served = True


@pytest.fixture(autouse=True)
def _no_real_bind(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralise the real bind + service construction for every test here."""
    _SpyUvicornServer.served = False
    monkeypatch.setattr(server_manager.uvicorn, "Server", _SpyUvicornServer)
    monkeypatch.setattr(server_manager.UnifiedServerManager, "_build_service", lambda _self: None)


@pytest.mark.parametrize("start", START_METHODS)
async def test_manager_refuses_public_bind_without_optin(
    monkeypatch: pytest.MonkeyPatch, start: str
) -> None:
    """A non-loopback host with no opt-in is refused BEFORE any bind happens."""
    monkeypatch.setattr(settings, "allow_public_bind", False)
    manager = server_manager.UnifiedServerManager()

    with pytest.raises(InsecureBindError):
        await getattr(manager, start)("0.0.0.0", 8000)

    assert _SpyUvicornServer.served is False, "guard must refuse BEFORE binding"


@pytest.mark.parametrize("start", START_METHODS)
async def test_manager_warns_loudly_and_proceeds_with_optin(
    monkeypatch: pytest.MonkeyPatch, start: str
) -> None:
    """With the explicit opt-in the public bind proceeds but is loudly warned."""
    monkeypatch.setattr(settings, "allow_public_bind", True)
    logger = _RecordingLogger()
    manager = server_manager.UnifiedServerManager(logger=logger)

    await getattr(manager, start)("0.0.0.0", 8000)

    assert _SpyUvicornServer.served is True, "opt-in bind must proceed to serve()"
    assert any("0.0.0.0" in w for w in logger.warnings), "opt-in must warn loudly"


async def test_manager_binds_loopback_without_optin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default loopback bind proceeds silently even with no opt-in."""
    monkeypatch.setattr(settings, "allow_public_bind", False)
    logger = _RecordingLogger()
    manager = server_manager.UnifiedServerManager(logger=logger)

    await manager.start_http_only_server("127.0.0.1", 8000)

    assert _SpyUvicornServer.served is True
    assert logger.warnings == []
