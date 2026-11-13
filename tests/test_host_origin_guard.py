"""Security contract for strict Host and Origin validation."""

from __future__ import annotations

import inspect
from importlib.metadata import version

import pytest
from fastapi.testclient import TestClient
from fastmcp import FastMCP
from packaging.version import Version
from pydantic import ValidationError

from ucsc_link import app as app_module
from ucsc_link import server_manager
from ucsc_link.config import ServerSettings, settings


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(
        settings,
        "allowed_hosts",
        ["localhost", "127.0.0.1", "::1", "ucsc-link.example.org"],
    )
    monkeypatch.setattr(
        settings,
        "allowed_origins",
        ["https://ucsc-link.example.org"],
    )
    return TestClient(app_module.create_app(), raise_server_exceptions=False)


def test_fastmcp_supports_native_strict_guard_configuration() -> None:
    assert Version(version("fastmcp")) >= Version("3.4.4")
    source = inspect.getsource(server_manager)
    assert "host_origin_protection=True" in source
    assert "allowed_hosts=settings.allowed_hosts" in source
    assert "allowed_origins=settings.allowed_origins" in source


async def test_unified_server_passes_exact_lists_to_native_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    original_http_app = FastMCP.http_app

    def spy_http_app(self: FastMCP, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        return original_http_app(self, *args, **kwargs)

    class FakeUvicornServer:
        def __init__(self, _config: object) -> None:
            self.should_exit = False

        async def serve(self) -> None:
            return None

    monkeypatch.setattr(FastMCP, "http_app", spy_http_app)
    monkeypatch.setattr(server_manager.uvicorn, "Server", FakeUvicornServer)
    monkeypatch.setattr(server_manager.UnifiedServerManager, "_build_service", lambda _self: None)
    manager = server_manager.UnifiedServerManager()
    await manager.start_unified_server("127.0.0.1", 8000)

    assert captured["host_origin_protection"] is True
    assert captured["allowed_hosts"] is settings.allowed_hosts
    assert captured["allowed_origins"] is settings.allowed_origins


@pytest.mark.parametrize(
    "host",
    ["localhost", "localhost:8000", "127.0.0.1:8000", "[::1]", "[::1]:8000"],
)
def test_loopback_hosts_are_allowed(client: TestClient, host: str) -> None:
    assert client.get("/health", headers={"Host": host}).status_code == 200


@pytest.mark.parametrize("host", ["ucsc-link.example.org", "ucsc-link.example.org:8443"])
def test_configured_public_host_is_allowed(client: TestClient, host: str) -> None:
    assert client.get("/health", headers={"Host": host}).status_code == 200


@pytest.mark.parametrize("path", ["/", "/health", "/docs", "/mcp"])
def test_unlisted_host_is_rejected_on_every_route(client: TestClient, path: str) -> None:
    assert client.get(path, headers={"Host": "attacker.example"}).status_code == 421


@pytest.mark.parametrize("path", ["/", "/health", "/docs", "/mcp"])
def test_unlisted_origin_is_rejected_on_every_route(client: TestClient, path: str) -> None:
    response = client.get(
        path,
        headers={"Host": "localhost", "Origin": "https://attacker.example"},
    )
    assert response.status_code == 403


@pytest.mark.parametrize("origin", [None, "https://ucsc-link.example.org"])
def test_absent_or_configured_origin_is_allowed(client: TestClient, origin: str | None) -> None:
    headers = {"Host": "localhost"}
    if origin is not None:
        headers["Origin"] = origin
    assert client.get("/health", headers=headers).status_code == 200


def test_default_empty_origin_allowlist_rejects_present_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "allowed_hosts", ["localhost"])
    monkeypatch.setattr(settings, "allowed_origins", [])
    default_client = TestClient(app_module.create_app(), raise_server_exceptions=False)
    response = default_client.get(
        "/health",
        headers={"Host": "localhost", "Origin": "https://browser.example"},
    )
    assert response.status_code == 403


def test_untrusted_preflight_is_rejected_by_outer_guard(client: TestClient) -> None:
    response = client.options(
        "/health",
        headers={
            "Host": "attacker.example",
            "Origin": "https://attacker.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 421


@pytest.mark.parametrize(
    ("field", "entry"),
    [
        ("allowed_hosts", "*"),
        ("allowed_hosts", "*.example.org"),
        ("allowed_hosts", "host?.example.org"),
        ("allowed_hosts", "host[0].example.org"),
        ("allowed_origins", "https://*.example.org"),
    ],
)
def test_wildcard_allowlist_entries_are_rejected(field: str, entry: str) -> None:
    with pytest.raises(ValidationError, match="wildcard"):
        ServerSettings(_env_file=None, **{field: [entry]})


def test_allowlists_load_from_prefixed_json_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UCSC_LINK_ALLOWED_HOSTS", '["api.example.org"]')
    monkeypatch.setenv("UCSC_LINK_ALLOWED_ORIGINS", '["https://app.example.org"]')
    configured = ServerSettings(_env_file=None)
    assert configured.allowed_hosts == ["api.example.org"]
    assert configured.allowed_origins == ["https://app.example.org"]
