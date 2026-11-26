import hashlib
import importlib.util
from pathlib import Path

from src.updater import parse_checksums

ROOT = Path(__file__).resolve().parent.parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_checksums_file_is_readable_by_the_updater(tmp_path):
    (tmp_path / "model_extended.onnx").write_bytes(b"abc")
    (tmp_path / "distribution_stats.json").write_bytes(b"{}")
    out = tmp_path / "checksums.sha256"
    _load("make_checksums").make(out, [tmp_path / "model_extended.onnx", tmp_path / "distribution_stats.json"])
    parsed = parse_checksums(out.read_text())
    assert parsed == {"model_extended.onnx": hashlib.sha256(b"abc").hexdigest(), "distribution_stats.json": hashlib.sha256(b"{}").hexdigest()}


def test_release_workflow_is_tag_triggered_and_publishes_checksums():
    wf = (ROOT / ".github" / "workflows" / "release.yml").read_text()
    assert "tags:" in wf and "'v*'" in wf and "pull_request" not in wf
    assert "windows-latest" in wf and "macos-latest" in wf
    assert "make_checksums.py" in wf and "checksums.sha256" in wf
    assert "draft: true" in wf  # a human publishes the release
    assert "RIOT_API_KEY" not in wf  # no secret in the release pipeline


def test_build_script_bundles_locales_and_assets():
    src = (ROOT / "scripts" / "build.py").read_text()
    assert "locales" in src and "assets" in src and "--windowed" in src
