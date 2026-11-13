"""Deploy-time fetch of UCSC Kent BLAT binaries and .2bit genomes (``ucsc-link-blat-fetch``).

The Kent tools are free for academic, non-profit and personal use only (commercial use
needs a Kent Informatics licence), so the public image never ships them: the
deployment's one-shot init container runs this to download them onto a named volume.
Every file is pinned by SHA-256 (the .2bit also by UCSC's own ``md5sum.txt``), streamed
to a ``.part`` file, verified, then atomically renamed. A present, valid file is kept.

Layout under DEST: ``bin/<tool>`` and ``<assembly>/<assembly>.2bit``. Binaries come from
UCSC's frozen ``linux.x86_64.v479`` directory, so the pinned bytes never move.

Usage: ``ucsc-link-blat-fetch DEST [NAME ...]`` (default: gfServer gfClient gfPcr hg38).
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx

from ucsc_link.api.client import USER_AGENT

_EXE = "https://hgdownload.soe.ucsc.edu/admin/exe/linux.x86_64.v479"
_GOLDEN = "https://hgdownload.soe.ucsc.edu/goldenPath"


class FetchError(RuntimeError):
    """A download failed or did not match its pin."""


@dataclass(frozen=True)
class Pin:
    """One pinned file: source URL, SHA-256, install path under DEST."""

    url: str
    sha256: str
    path: str
    md5: str | None = None
    executable: bool = False


PINS: dict[str, Pin] = {
    "gfServer": Pin(
        f"{_EXE}/blat/gfServer",
        "c06559a5c9ca52006805c6d37460b31a2c869184321790a8fb7f58db48db6959",
        "bin/gfServer",
        executable=True,
    ),
    "gfClient": Pin(
        f"{_EXE}/blat/gfClient",
        "6394d9607433cd20076e10fd5d6df5f3b4b390942122f726e27f8dd2a9b7d407",
        "bin/gfClient",
        executable=True,
    ),
    "gfPcr": Pin(
        f"{_EXE}/blat/gfPcr",
        "3b1328589b716633674864b8e07b1068e1d82ba82e438249bf9afb71d2814949",
        "bin/gfPcr",
        executable=True,
    ),
    # Only needed to build test genomes (CI); never fetched by the deployment.
    "faToTwoBit": Pin(
        f"{_EXE}/faToTwoBit",
        "42fb2917ba0f7ef6e790be3d0d53502b8b57f1255ec00a740c168e0088c5ea98",
        "bin/faToTwoBit",
        executable=True,
    ),
    "hg38": Pin(
        f"{_GOLDEN}/hg38/bigZips/hg38.2bit",
        "1f67aaa17a77b327738fe750ab37430a85dddf73c7d9189385ad1839259acec0",
        "hg38/hg38.2bit",
        md5="dcc3ea27079aa6dc3f9deccd7275e0f8",
    ),
    "hg19": Pin(
        f"{_GOLDEN}/hg19/bigZips/hg19.2bit",
        "797bdb4391cbe23bd30268d41d0d86676fd7b36b7f8e76797b1882d585766fa3",
        "hg19/hg19.2bit",
        md5="bcdbfbe9da62f19bee88b74dabef8cd3",
    ),
}
DEFAULT_NAMES = ("gfServer", "gfClient", "gfPcr", "hg38")


def _say(line: str) -> None:
    sys.stderr.write(line + "\n")  # CLI progress; stdout stays clean


def _digests(path: Path) -> tuple[str, str]:
    sha, md5 = hashlib.sha256(), hashlib.md5(usedforsecurity=False)
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            sha.update(chunk)
            md5.update(chunk)
    return sha.hexdigest(), md5.hexdigest()


def _valid(path: Path, pin: Pin) -> str | None:
    """None if the file matches its pin, else which digest failed."""
    sha, md5 = _digests(path)
    if sha != pin.sha256:
        return "sha256"
    if pin.md5 is not None and md5 != pin.md5:
        return "md5"
    return None


def _download(client: httpx.Client, pin: Pin, target: Path) -> None:
    part = target.with_name(target.name + ".part")
    try:
        with client.stream("GET", pin.url) as response:
            if response.status_code != 200:
                raise FetchError(f"{pin.url}: HTTP {response.status_code}")
            with part.open("wb") as handle:
                for chunk in response.iter_bytes(1 << 20):
                    handle.write(chunk)
        if (failed := _valid(part, pin)) is not None:
            raise FetchError(f"{pin.url}: {failed} mismatch against the pinned checksum")
        part.chmod(0o555 if pin.executable else 0o444)
        part.replace(target)
    except httpx.HTTPError as exc:
        raise FetchError(f"{pin.url}: {exc.__class__.__name__}") from None
    finally:
        with contextlib.suppress(FileNotFoundError):
            part.unlink()


def fetch(dest: Path, names: Sequence[str], *, pins: dict[str, Pin] = PINS) -> None:
    """Ensure every named pin is present and verified under ``dest``."""
    unknown = [name for name in names if name not in pins]
    if unknown:
        raise FetchError(f"unknown pin(s) {unknown}; known: {sorted(pins)}")
    with httpx.Client(
        headers={"User-Agent": USER_AGENT}, timeout=httpx.Timeout(60.0), follow_redirects=False
    ) as client:
        for name in names:
            pin = pins[name]
            target = dest / pin.path
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_file() and _valid(target, pin) is None:
                _say(f"ok      {pin.path} (already verified)")
                continue
            _say(f"fetch   {pin.url}")
            _download(client, pin, target)
            _say(f"ok      {pin.path} sha256={pin.sha256[:12]}...")


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point for the deploy-time init container and CI."""
    parser = argparse.ArgumentParser(prog="ucsc-link-blat-fetch", description=__doc__)
    parser.add_argument("dest", type=Path, help="Target directory (the shared volume).")
    parser.add_argument("names", nargs="*", default=list(DEFAULT_NAMES), help=str(sorted(PINS)))
    args = parser.parse_args(argv)
    os.umask(0o022)
    try:
        fetch(args.dest, args.names)
    except (FetchError, OSError) as exc:
        _say(f"error   {exc}")
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
