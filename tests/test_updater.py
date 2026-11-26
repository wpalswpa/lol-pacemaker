import hashlib
import json
import urllib.error
from pathlib import Path

import pytest

from ml.train import train_and_export_onnx
from src import updater
from src.predictor import LocalOnnxPredictor, ScoreboardState
from tests.synth import many_rows


@pytest.fixture(scope="module")
def model_files(tmp_path_factory) -> dict[str, bytes]:
    rows, players = many_rows()
    d = tmp_path_factory.mktemp("trained")
    train_and_export_onnx(rows, d, player_rows=players)
    return {n: (d / n).read_bytes() for n in updater.MODEL_FILES}


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class FakeRelease:
    """In-memory GitHub release + fetcher; counts calls and can tamper with any file."""

    def __init__(self, files: dict[str, bytes], tag: str = "v1", tamper: str | None = None, https: bool = True):
        self.tag = tag
        scheme = "https" if https else "http"
        self.blobs = {f"{scheme}://dl.example/{n}": b for n, b in files.items()}
        listed = {n: sha(b) for n, b in files.items()}
        self.blobs[f"{scheme}://dl.example/{updater.CHECKSUMS_FILE}"] = "".join(f"{h}  {n}\n" for n, h in listed.items()).encode()
        if tamper:
            self.blobs[f"{scheme}://dl.example/{tamper}"] += b"X"  # bytes no longer match the published hash
        self.assets = [{"name": u.rsplit("/", 1)[1], "browser_download_url": u} for u in self.blobs]

    def fetch(self, url: str, max_bytes: int) -> bytes:
        if url == updater.RELEASES_URL:
            return json.dumps({"tag_name": self.tag, "assets": self.assets}).encode()
        return self.blobs[url]


def test_valid_release_installs_and_predictor_uses_it(model_files):
    res = updater.check_for_update(FakeRelease(model_files).fetch)
    assert res.status == "updated" and res.version == "v1"
    d = updater.current_model_dir()
    assert d is not None and (d / "model_extended.onnx").read_bytes() == model_files["model_extended.onnx"]
    pred = LocalOnnxPredictor()
    assert pred.mode == "extended" and pred.stats is not None
    assert 0.0 <= pred.predict_win_prob(ScoreboardState(blue_kills=5)) <= 1.0
    assert updater.check_for_update(FakeRelease(model_files).fetch).status == "up_to_date"


@pytest.mark.parametrize("victim", ["model_extended.onnx", "distribution_stats.json"])
def test_tampered_file_is_rejected_and_builtin_model_is_used(model_files, victim):
    from src.predictor import DEFAULT_ASSETS_DIR

    res = updater.check_for_update(FakeRelease(model_files, tamper=victim).fetch)
    print("tamper", victim, "->", res)  # shown with -s / -rA
    assert res.status == "rejected" and "SHA-256 mismatch" in res.reason
    assert updater.current_model_dir() is None
    pred = LocalOnnxPredictor()
    assert pred.source is not None and pred.source.parent == DEFAULT_ASSETS_DIR  # built-in fallback keeps working
    assert 0.0 <= pred.predict_win_prob(ScoreboardState()) <= 1.0


def test_failed_update_keeps_previous_good_model(model_files):
    assert updater.check_for_update(FakeRelease(model_files, tag="v1").fetch).status == "updated"
    before = (updater.models_dir() / "current.json").read_text()
    res = updater.check_for_update(FakeRelease(model_files, tag="v2", tamper="model_extended.onnx").fetch)
    assert res.status == "rejected"
    assert (updater.models_dir() / "current.json").read_text() == before
    assert updater.current_version() == "v1" and LocalOnnxPredictor().mode == "extended"
    assert not (updater.models_dir() / "v2").exists()
    assert not [p for p in updater.models_dir().iterdir() if p.name.startswith(".stage")]  # no temp leftovers


def test_hash_valid_but_garbage_onnx_is_rejected_by_validation(model_files):
    bad = {**model_files, "model_extended.onnx": b"not an onnx file"}
    res = updater.check_for_update(FakeRelease(bad).fetch)
    assert res.status == "failed" and updater.current_model_dir() is None


def test_http_asset_url_is_rejected(model_files):
    assert updater.check_for_update(FakeRelease(model_files, https=False).fetch).status == "rejected"


def test_missing_checksum_entry_and_missing_asset(model_files):
    rel = FakeRelease(model_files)
    only_onnx = f"{sha(model_files['model_extended.onnx'])}  model_extended.onnx\n".encode()
    rel.blobs[f"https://dl.example/{updater.CHECKSUMS_FILE}"] = only_onnx
    assert updater.check_for_update(rel.fetch).status == "rejected"
    rel2 = FakeRelease(model_files)
    rel2.assets = [a for a in rel2.assets if a["name"] != "distribution_stats.json"]
    assert updater.check_for_update(rel2.fetch).status == "rejected"


def test_network_error_and_bad_tag_do_not_raise(model_files):
    def boom(url, max_bytes):
        raise urllib.error.URLError("offline")

    assert updater.check_for_update(boom).status == "failed"
    rel = FakeRelease(model_files, tag="../../etc")
    assert updater.check_for_update(rel.fetch).status == "rejected"


def test_corrupt_installed_model_falls_back_to_builtin(model_files):
    from src.predictor import DEFAULT_ASSETS_DIR

    updater.check_for_update(FakeRelease(model_files).fetch)
    (updater.current_model_dir() / "model_extended.onnx").write_bytes(b"corrupted after install")
    pred = LocalOnnxPredictor()
    assert pred.source is not None and pred.source.parent == DEFAULT_ASSETS_DIR and pred.load_errors


def test_https_only_transport():
    with pytest.raises(ValueError):
        updater.https_fetch("http://example.com/x", 10)
    handler = updater._HttpsOnlyRedirect()
    with pytest.raises(urllib.error.URLError):
        handler.redirect_request(None, None, 302, "Found", {}, "http://evil.example/x")


def test_parse_checksums_rejects_malformed():
    assert updater.parse_checksums(f"{'a' * 64}  f.onnx\n# c\n") == {"f.onnx": "a" * 64}
    with pytest.raises(ValueError):
        updater.parse_checksums("deadbeef  f.onnx")
