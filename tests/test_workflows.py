"""images.yml: a PR builds both images without pushing; main and v* tags publish them to GHCR."""
from pathlib import Path

import yaml

WF = Path(__file__).resolve().parent.parent / ".github" / "workflows"


def test_images_workflow():
    text = (WF / "images.yml").read_text()
    doc = yaml.safe_load(text)
    on = doc[True]  # YAML 1.1 reads the bare key `on` as True
    assert "pull_request" in on
    assert on["push"]["branches"] == ["main"] and on["push"]["tags"] == ["v*"]
    jobs = doc["jobs"]
    assert set(jobs) == {"build", "publish"}
    build = yaml.safe_dump(jobs["build"])
    assert "push: true" not in build and "login-action" not in build
    assert jobs["build"]["if"] == "github.event_name == 'pull_request'"
    assert jobs["publish"]["if"] == "github.event_name == 'push'"
    assert jobs["publish"]["permissions"]["packages"] == "write"
    for name in ("ghcr.io/white-bridge-labs/ffl-api", "ghcr.io/white-bridge-labs/ffl-web", "linux/amd64,linux/arm64"):
        assert name in yaml.safe_dump(jobs["publish"])
    for job in jobs.values():
        for step in job["steps"]:
            if "uses" in step:  # pinned by SHA, as test.yml is
                assert len(step["uses"].split("@")[1]) == 40, step["uses"]


def test_no_deploy_workflow_left():
    assert not (WF / "deploy.yml").exists()
    header = (WF / "test.yml").read_text().split("\non:")[0]
    assert "deploy.yml" not in header


def test_latest_from_main():
    """`is_default_branch` is develop here, and the workflow runs on main: latest must name main itself."""
    text = (WF / "images.yml").read_text()
    rule = next(l for l in text.splitlines() if "value=latest" in l)
    assert "enable=${{ github.ref == 'refs/heads/main' }}" in rule
