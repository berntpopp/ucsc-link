# tests/test_config.py
import pytest

from ucsc_link.config import (
    InsecureBindError,
    ServerSettings,
    check_bind_safety,
    is_loopback_host,
)


class _RecordingLogger:
    """Minimal logger stub that records warning calls."""

    def __init__(self) -> None:
        self.warnings: list[str] = []

    def warning(self, event: str, *args: object, **kwargs: object) -> None:
        self.warnings.append(event)


def test_settings_defaults():
    s = ServerSettings()
    assert s.port == 8000
    assert s.ucsc.base_url == "https://api.genome.ucsc.edu"
    assert s.ucsc.min_interval_s == 1.0 and s.ucsc.daily_cap == 5000
    assert s.transport in {"unified", "http", "stdio"}


# -- F-04: loopback-default bind guard -----------------------------------------


def test_default_host_is_loopback():
    """The unauthenticated backend must default to a loopback bind (F-04)."""
    assert ServerSettings().host == "127.0.0.1"
    # And the public-bind opt-in must default OFF (fail-closed).
    assert ServerSettings().allow_public_bind is False


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1", True),
        ("127.0.0.5", True),
        ("::1", True),
        ("localhost", True),
        ("LOCALHOST", True),
        ("0.0.0.0", False),
        ("::", False),
        ("192.168.1.10", False),
        ("ucsc-link.example.org", False),
        ("", False),
    ],
)
def test_is_loopback_host(host: str, expected: bool):
    assert is_loopback_host(host) is expected


def test_check_bind_safety_allows_loopback_silently():
    logger = _RecordingLogger()
    check_bind_safety("127.0.0.1", allow_public=False, logger=logger)
    assert logger.warnings == []


def test_check_bind_safety_refuses_public_without_optin():
    """A non-loopback bind without the explicit opt-in is refused (fail-closed)."""
    with pytest.raises(InsecureBindError):
        check_bind_safety("0.0.0.0", allow_public=False, logger=_RecordingLogger())


def test_check_bind_safety_warns_loudly_with_optin():
    """With the opt-in the public bind proceeds but is loudly logged."""
    logger = _RecordingLogger()
    check_bind_safety("0.0.0.0", allow_public=True, logger=logger)
    assert logger.warnings, "an explicit public bind must emit a loud warning"
    assert any("0.0.0.0" in w for w in logger.warnings)


def test_strict_rate_limit_reading_is_configurable_from_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("UCSC_LINK_UCSC__MIN_INTERVAL_S", "15")
    monkeypatch.setenv("UCSC_LINK_UCSC__DAILY_CAP", "5000")
    s = ServerSettings()
    assert s.ucsc.min_interval_s == 15.0 and s.ucsc.daily_cap == 5000
