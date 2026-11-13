"""Build-hardening guard: the Docker builder must not bootstrap a floating pip/uv.

F-19: ``docker/Dockerfile`` previously ran ``pip install --upgrade pip uv``, an
unbounded upgrade that makes the image non-reproducible. The uv binary must come
from a digest-pinned ``COPY --from`` instead. Research use only; not clinical
decision support.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_UV_PIN = (
    "ghcr.io/astral-sh/uv:0.8.7@sha256:"
    "1e26f9a868360eeb32500a35e05787ffff3402f01a8dc8168ef6aee44aef0aab"
)


def test_dockerfile_pins_uv_and_has_no_floating_pip_upgrade() -> None:
    text = (ROOT / "docker" / "Dockerfile").read_text(encoding="utf-8")
    assert "pip install --upgrade" not in text, "floating pip/uv upgrade must be removed"
    assert _UV_PIN in text, "uv must be installed from a digest-pinned COPY --from"
    # The digest-pinned uv is COPYed in, not upgraded in place.
    assert f"COPY --from={_UV_PIN} /uv /usr/local/bin/uv" in text


def test_prepared_stage_applies_current_debian_security_upgrades() -> None:
    prepared_stage = (
        (ROOT / "docker" / "Dockerfile")
        .read_text(encoding="utf-8")
        .split(" AS prepared", maxsplit=1)[1]
    )
    assert "apt-get upgrade -y --no-install-recommends" in prepared_stage


def test_final_image_removes_pip_vendor_packages_after_install() -> None:
    text = (ROOT / "docker" / "Dockerfile").read_text(encoding="utf-8")
    prepared_stage = text.split(" AS prepared", maxsplit=1)[1]

    assert "pip install . --no-deps" in prepared_stage
    assert "/usr/local/lib/python*/site-packages/pip" in prepared_stage
    assert "/opt/venv/lib/python*/site-packages/pip" in prepared_stage
