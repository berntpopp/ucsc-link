"""Configuration management for ucsc-link.

Settings load from environment variables with the ``UCSC_LINK_`` prefix
(nested models use ``__``, e.g. ``UCSC_LINK_UCSC__BASE_URL=...``) and an
optional ``.env`` file.

ucsc-link is a **live-API proxy**: it calls the official UCSC JSON API over async
httpx with a short-lived in-process cache. There is no data snapshot or ingest step.
"""

from __future__ import annotations

import ipaddress
import math
import re
from contextlib import suppress
from typing import Annotated, Any, Literal, cast

from pydantic import BaseModel, BeforeValidator, Field, StrictInt, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class InsecureBindError(RuntimeError):
    """A non-loopback bind was requested without the explicit public-bind opt-in."""


def _strict_finite_float(value: object) -> object:
    """Reject coercion, booleans, non-finite values and huge integers in settings."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("must be a finite numeric value, not a coercible value")
    numeric = value
    try:
        if not math.isfinite(numeric):
            raise ValueError("must be finite")
    except (OverflowError, TypeError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc) == "must be finite":
            raise
        raise ValueError("must be finite") from None
    return numeric


def validate_finite_seconds(value: object, *, maximum: float) -> float:
    """Validate a bounded runtime duration for callers outside Pydantic."""
    try:
        valid = not isinstance(value, bool) and isinstance(value, (int, float))
        numeric = cast(int | float, value)
        valid = valid and math.isfinite(numeric) and 0 < numeric <= maximum
    except OverflowError, TypeError, ValueError:
        valid = False
    if not valid:
        raise ValueError(f"must be a finite value in (0, {maximum}]")
    return float(numeric)


FiniteFloat = Annotated[float, BeforeValidator(_strict_finite_float)]


def is_loopback_host(host: str) -> bool:
    """Return True if binding ``host`` exposes only the loopback interface.

    ``localhost`` and any address in ``127.0.0.0/8`` or ``::1`` are loopback. The
    wildcard binds (``0.0.0.0`` / ``::``) and every routable address are NOT.
    """
    candidate = host.strip().lower()
    if candidate == "localhost":
        return True
    try:
        return ipaddress.ip_address(candidate).is_loopback
    except ValueError:
        # Any other hostname (empty, a public DNS name) is treated as non-loopback.
        return False


def check_bind_safety(host: str, *, allow_public: bool, logger: Any = None) -> None:
    """Fail-closed guard for the direct-run bind interface (F-04).

    ucsc-link is **unauthenticated by design** and must sit behind the router
    / reverse proxy. A loopback bind is always safe. A non-loopback bind requires
    the explicit ``UCSC_LINK_ALLOW_PUBLIC_BIND`` opt-in: without it startup is
    refused (``InsecureBindError``); WITH it the server still emits a loud warning
    so the exposure is audited.
    """
    if is_loopback_host(host):
        return
    if not allow_public:
        raise InsecureBindError(
            f"Refusing to bind non-loopback interface {host!r}: ucsc-link is "
            "unauthenticated and must sit behind the router/reverse proxy. Bind "
            "127.0.0.1, or set UCSC_LINK_ALLOW_PUBLIC_BIND=true to opt in "
            "(only when a trusted proxy terminates access in front of it)."
        )
    if logger is not None:
        logger.warning(
            f"INSECURE PUBLIC BIND: binding the non-loopback interface {host!r} with "
            "UCSC_LINK_ALLOW_PUBLIC_BIND=true. This unauthenticated backend is "
            "now reachable off-host; ensure a trusted reverse proxy terminates "
            "access in front of it."
        )


class UcscSettings(BaseModel):
    """Upstream UCSC JSON API client settings (base URL, politeness, retries, cache)."""

    base_url: str = Field(
        default="https://api.genome.ucsc.edu",
        description="Base URL of the official UCSC Genome Browser JSON API (no auth).",
    )
    request_timeout_s: FiniteFloat = Field(
        default=20.0, gt=0, le=120, description="Per-request HTTP timeout (seconds)."
    )
    min_interval_s: FiniteFloat = Field(
        default=1.0,
        ge=0,
        le=60,
        description=(
            "Minimum spacing between upstream requests, process-wide. UCSC's API help "
            "recommends <=1 hit/second; its Conditions of Use say 1 hit per 15 s for "
            "program-driven use. Set 15 for the strict reading."
        ),
    )
    daily_cap: StrictInt = Field(
        default=5000, ge=1, le=100_000, description="Max upstream requests per UTC day."
    )
    max_wait_s: FiniteFloat = Field(
        default=20.0,
        gt=0,
        le=300,
        description="Longest queue wait for a request slot before answering rate_limited.",
    )
    max_retries: StrictInt = Field(
        default=2, ge=0, le=5, description="Retries on 429/5xx/timeout (with backoff)."
    )
    backoff_base_s: FiniteFloat = Field(
        default=2.0, ge=0, le=60, description="Exponential backoff base (seconds)."
    )
    max_response_bytes: StrictInt = Field(
        default=16 * 1024 * 1024,
        ge=1,
        le=64 * 1024 * 1024,
        description="Hard cap on one upstream response body (fail-closed, never truncate).",
    )
    cache_ttl_s: StrictInt = Field(
        default=3600, ge=0, le=86_400, description="In-process response cache TTL (seconds)."
    )
    cache_maxsize: StrictInt = Field(
        default=512, ge=1, le=10_000, description="In-process response cache entries."
    )


class BlatSettings(BaseModel):
    """Self-hosted BLAT / in-silico PCR (UCSC Kent gfServer) settings.

    Empty ``servers`` means the feature is not deployed: the tools then answer
    ``upstream_unavailable`` and capabilities/diagnostics say so.
    """

    servers: dict[Literal["hg38", "hg19"], str] = Field(
        default_factory=dict,
        description='Assembly -> gfServer "host:port", e.g. {"hg38": "ucsc-gfserver-hg38:8000"}.',
    )
    data_dir: str = Field(
        default="/opt/blat",
        description="Kent binaries in <data_dir>/bin, genomes as <data_dir>/<asm>/<asm>.2bit.",
    )
    timeout_s: FiniteFloat = Field(
        default=60.0, gt=0, le=600, description="Wall-clock limit per gfClient/gfPcr run."
    )
    max_concurrency: StrictInt = Field(
        default=2, ge=1, le=16, description="Concurrent gfClient/gfPcr subprocesses."
    )
    tmp_dir: str | None = Field(
        default=None, description="Scratch dir for query/result files (default: TMPDIR)."
    )

    @field_validator("servers")
    @classmethod
    def validate_servers(cls, value: dict[str, str]) -> dict[str, str]:
        """Require plain ``host:port`` endpoints."""
        for endpoint in value.values():
            if not re.fullmatch(r"[A-Za-z0-9.-]{1,253}:[0-9]{1,5}", endpoint):
                raise ValueError('gfServer endpoint must be "host:port"')
        return value


class ServerSettings(BaseSettings):
    """Top-level server settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        env_prefix="UCSC_LINK_",
        env_nested_delimiter="__",
    )

    host: str = Field(
        default="127.0.0.1",
        description="Server bind host (loopback by default; see allow_public_bind).",
    )
    allow_public_bind: bool = Field(
        default=False,
        description=(
            "Opt in to a non-loopback (public) bind. Without it a non-loopback host "
            "is refused at startup: ucsc-link is unauthenticated and must sit "
            "behind the router/reverse proxy."
        ),
    )
    port: int = Field(default=8000, ge=1024, le=65535, description="Server port.")

    transport: Literal["unified", "http", "stdio"] = Field(
        default="unified",
        description="Server transport mode.",
    )
    mcp_path: str = Field(default="/mcp", description="MCP endpoint path.")
    allowed_hosts: list[str] = Field(
        default=["localhost", "127.0.0.1", "::1"],
        description="Exact HTTP Host allowlist.",
    )
    allowed_origins: list[str] = Field(default=[], description="Exact HTTP Origin allowlist.")

    cors_origins: list[str] = Field(
        default=[],
        description="Allowed CORS origins.",
    )

    log_level: str = Field(default="INFO", description="Logging level.")
    log_format: Literal["json", "console"] = Field(
        default="console",
        description="Log format.",
    )

    ucsc: UcscSettings = Field(
        default_factory=UcscSettings,
        description="Upstream UCSC JSON API client configuration.",
    )

    blat: BlatSettings = Field(
        default_factory=BlatSettings,
        description="Self-hosted BLAT / in-silico PCR (gfServer) configuration.",
    )

    @field_validator("ucsc", mode="before")
    @classmethod
    def parse_numeric_environment_values(cls, value: Any) -> Any:
        """Decode documented decimal integer/float strings from nested environments."""
        if not isinstance(value, dict):
            return value
        parsed = dict(value)
        for name in ("request_timeout_s", "min_interval_s", "max_wait_s", "backoff_base_s"):
            raw = parsed.get(name)
            if isinstance(raw, str):
                with suppress(ValueError):
                    parsed[name] = float(raw)
        for name in (
            "daily_cap",
            "max_retries",
            "max_response_bytes",
            "cache_ttl_s",
            "cache_maxsize",
        ):
            raw = parsed.get(name)
            if isinstance(raw, str) and re.fullmatch(r"[+-]?\d+", raw.strip()):
                parsed[name] = int(raw)
        return parsed

    @field_validator("mcp_path")
    @classmethod
    def validate_mcp_path(cls, v: str) -> str:
        """Ensure the MCP path starts with a forward slash."""
        return v if v.startswith("/") else f"/{v}"

    @field_validator("allowed_hosts", "allowed_origins")
    @classmethod
    def reject_allowlist_wildcards(cls, values: list[str]) -> list[str]:
        """Require exact entries because FastMCP supports glob matching."""
        if any(character in entry for entry in values for character in "*?[]"):
            raise ValueError("wildcard entries are not permitted in HTTP allowlists")
        return values

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: Any) -> list[str]:
        """Parse CORS origins from a comma-separated string or a list."""
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return list(v) if v else []


settings = ServerSettings()
