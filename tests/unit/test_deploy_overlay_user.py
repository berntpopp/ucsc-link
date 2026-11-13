"""Guard: the deployed NPM overlay must declare a numeric non-root `user` for
the fleet controller's runtime observer, while the release Compose files that
`container-release.json` lists must NOT declare `user` (the release gate forbids it)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]  # tests/unit/ -> repo root

NUMERIC_USER_RE = re.compile(r"^[1-9][0-9]*:[1-9][0-9]*$")


class _TagTolerantLoader(yaml.SafeLoader):
    """SafeLoader that tolerates custom Compose override tags like !reset/!override."""


_TagTolerantLoader.add_multi_constructor(
    "!",
    lambda loader, suffix, node: (
        loader.construct_scalar(node) if isinstance(node, yaml.ScalarNode) else None
    ),
)


def _load_compose(path: Path) -> dict:
    # _TagTolerantLoader subclasses yaml.SafeLoader (no arbitrary object construction);
    # ruff's bandit rule only special-cases the literal SafeLoader/CSafeLoader classes.
    return yaml.load(path.read_text(encoding="utf-8"), Loader=_TagTolerantLoader)  # noqa: S506


def test_npm_overlay_declares_numeric_user_for_every_service() -> None:
    compose = _load_compose(ROOT / "docker" / "docker-compose.npm.yml")
    services = compose["services"]
    assert services, "docker-compose.npm.yml should declare at least one service"
    for name, svc in services.items():
        user = svc.get("user")
        assert user is not None, f"{name} in docker-compose.npm.yml must declare user"
        assert NUMERIC_USER_RE.match(str(user)), (
            f"{name} declares user={user!r} in docker-compose.npm.yml; the fleet "
            "controller's runtime observer requires a numeric non-root uid:gid"
        )


def test_release_compose_files_do_not_declare_user() -> None:
    release_config = json.loads((ROOT / "container-release.json").read_text(encoding="utf-8"))
    compose_files = release_config["service"]["compose_files"]
    assert compose_files, "container-release.json should list at least one compose file"
    for rel_path in compose_files:
        compose = _load_compose(ROOT / rel_path)
        for name, svc in compose["services"].items():
            assert "user" not in svc, (
                f"{name} in {rel_path} declares user={svc.get('user')!r}; the release "
                "gate (container_release.py validate-compose) forbids `user` in release "
                "Compose files"
            )
