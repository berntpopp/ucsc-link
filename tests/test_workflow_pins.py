"""Release workflow pins must track the trusted fleet revisions."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# genefoundry-router v0.10.17. Both container-ci.yml and container-release.yml load
# ReleaseConfig from their pinned commit, so both must track the same revision.
ROUTER_SHA = "2cd86e35373cc7b1bdd8686ed9c69ca584b00ed9"
SETUP_UV_SHA = "68559e99cc807c1eb7e9238fd43a15bba460eec2"
CODEQL_SHA = "33d7b85da1a04171e211befe2c739303b9a4c066"


def test_release_workflows_pin_the_trusted_router_and_actions() -> None:
    workflows = ROOT / ".github" / "workflows"
    release_text = (workflows / "container-release.yml").read_text(encoding="utf-8")
    ci_text = (workflows / "container-ci.yml").read_text(encoding="utf-8")
    assert ROUTER_SHA in release_text
    assert ROUTER_SHA in ci_text
    assert SETUP_UV_SHA in (workflows / "ci.yml").read_text(encoding="utf-8")
    security_text = (workflows / "security.yml").read_text(encoding="utf-8")
    assert security_text.count(CODEQL_SHA) == 2
