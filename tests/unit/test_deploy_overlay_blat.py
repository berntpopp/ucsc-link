"""Guard: the deployed overlay runs BLAT as init (fetch) -> gfServer -> app, all on the
release image, with the Kent binaries + 2bit on a named volume that only the init writes.

The release Compose files (CI smoke, conformance) carry no BLAT services: the app must
start healthy without it and report the feature as unavailable.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.unit.test_deploy_overlay_user import _load_compose

ROOT = Path(__file__).resolve().parents[2]
NPM = _load_compose(ROOT / "docker" / "docker-compose.npm.yml")["services"]
IMAGE = NPM["ucsc_link"]["image"]


def _mounts(service: dict) -> list[str]:
    return [str(m) for m in service.get("volumes", [])]


def test_fetch_is_a_run_once_init_on_the_release_image_writing_the_volume() -> None:
    fetch = NPM["ucsc-blat-fetch"]
    assert fetch["image"] == IMAGE and fetch["restart"] == "no"
    assert fetch["entrypoint"][:2] == ["ucsc-link-blat-fetch", "/opt/blat"]
    assert {"gfServer", "gfClient", "gfPcr", "hg38"} <= set(fetch["entrypoint"][2:])
    assert _mounts(fetch) == ["ucsc-blat:/opt/blat"]


def test_gfserver_is_internal_hardened_and_reads_the_volume_only() -> None:
    server = NPM["ucsc-gfserver-hg38"]
    assert server["image"] == IMAGE and server["user"] == "10001:10001"
    assert server["depends_on"] == {
        "ucsc-blat-fetch": {"condition": "service_completed_successfully"}
    }
    assert server["entrypoint"][:5] == [
        "/opt/blat/bin/gfServer",
        "start",
        "0.0.0.0",  # noqa: S104 - internal network only, never published
        "8000",
        "-stepSize=5",
    ]
    assert server["working_dir"] == "/opt/blat/hg38"
    assert _mounts(server) == ["ucsc-blat:/opt/blat:ro"]
    assert "ports" not in server and server["networks"] == ["ucsc_link_internal_net"]
    assert server["read_only"] is True and server["cap_drop"] == ["ALL"]
    assert server["security_opt"] == ["no-new-privileges:true"]
    assert server["healthcheck"]["test"][1] == "/opt/blat/bin/gfServer"
    assert server["deploy"]["resources"]["limits"]["memory"]


def test_app_reads_the_volume_and_does_not_wait_for_blat() -> None:
    app = NPM["ucsc_link"]
    assert "ucsc-blat:/opt/blat:ro" in _mounts(app)
    assert "depends_on" not in app  # live-API tools must not wait for a 1 GB download
    env = "\n".join(app["environment"])
    assert '"hg38":"ucsc-gfserver-hg38:8000"' in env


def test_release_compose_files_carry_no_blat_services() -> None:
    config = json.loads((ROOT / "container-release.json").read_text(encoding="utf-8"))
    for rel_path in config["service"]["compose_files"]:
        services = _load_compose(ROOT / rel_path)["services"]
        assert set(services) == {"ucsc-link"}


def test_image_prepares_the_volume_mount_point_for_the_app_user() -> None:
    text = (ROOT / "docker" / "Dockerfile").read_text(encoding="utf-8")
    assert "install -d -o app -g app -m 0755 /opt/blat" in text
