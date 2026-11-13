"""Deploy-time fetch of the Kent binaries and .2bit genomes: checksum-pinned, idempotent."""

from __future__ import annotations

import hashlib
import pathlib

import httpx
import pytest
import respx

from ucsc_link.blat_fetch import PINS, FetchError, Pin, fetch

BODY = b"#!/bin/sh\necho kent\n"
SHA = hashlib.sha256(BODY).hexdigest()
MD5 = hashlib.md5(BODY, usedforsecurity=False).hexdigest()
URL = "https://hgdownload.example/tool"


def _pins(**extra: Pin) -> dict[str, Pin]:
    return {"tool": Pin(URL, SHA, "bin/tool", executable=True), **extra}


def test_every_pin_is_https_ucsc_and_sha256_pinned() -> None:
    assert {"gfServer", "gfClient", "gfPcr", "faToTwoBit", "hg38", "hg19"} <= set(PINS)
    for pin in PINS.values():
        assert pin.url.startswith("https://hgdownload.soe.ucsc.edu/")
        assert len(pin.sha256) == 64
    assert PINS["hg38"].md5 == "dcc3ea27079aa6dc3f9deccd7275e0f8"  # UCSC md5sum.txt
    assert PINS["hg38"].path == "hg38/hg38.2bit"


def test_downloads_verifies_and_installs_executable(tmp_path: pathlib.Path) -> None:
    with respx.mock() as router:
        router.get(URL).mock(return_value=httpx.Response(200, content=BODY))
        fetch(tmp_path, ["tool"], pins=_pins())
    exe = tmp_path / "bin" / "tool"
    assert exe.read_bytes() == BODY and exe.stat().st_mode & 0o111
    assert not list(tmp_path.rglob("*.part"))


def test_checksum_mismatch_fails_closed_and_leaves_nothing(tmp_path: pathlib.Path) -> None:
    with respx.mock() as router:
        router.get(URL).mock(return_value=httpx.Response(200, content=b"tampered"))
        with pytest.raises(FetchError, match="sha256"):
            fetch(tmp_path, ["tool"], pins=_pins())
    assert not (tmp_path / "bin" / "tool").exists()
    assert not list(tmp_path.rglob("*.part"))


def test_md5_is_checked_too(tmp_path: pathlib.Path) -> None:
    pins = {"g": Pin(URL, SHA, "g/g.2bit", md5="0" * 32)}
    with respx.mock() as router:
        router.get(URL).mock(return_value=httpx.Response(200, content=BODY))
        with pytest.raises(FetchError, match="md5"):
            fetch(tmp_path, ["g"], pins=pins)


def test_present_and_valid_file_is_not_downloaded_again(tmp_path: pathlib.Path) -> None:
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "tool").write_bytes(BODY)
    with respx.mock(assert_all_called=False) as router:
        route = router.get(URL).mock(return_value=httpx.Response(200, content=BODY))
        fetch(tmp_path, ["tool"], pins=_pins())
    assert route.call_count == 0


def test_corrupt_existing_file_is_replaced(tmp_path: pathlib.Path) -> None:
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "tool").write_bytes(b"half a downl")
    with respx.mock() as router:
        router.get(URL).mock(return_value=httpx.Response(200, content=BODY))
        fetch(tmp_path, ["tool"], pins=_pins())
    assert (tmp_path / "bin" / "tool").read_bytes() == BODY


def test_http_error_is_a_fetch_error(tmp_path: pathlib.Path) -> None:
    with respx.mock() as router:
        router.get(URL).mock(return_value=httpx.Response(503))
        with pytest.raises(FetchError):
            fetch(tmp_path, ["tool"], pins=_pins())


def test_unknown_name_is_refused(tmp_path: pathlib.Path) -> None:
    with pytest.raises(FetchError, match="unknown"):
        fetch(tmp_path, ["hs1"], pins=_pins())
