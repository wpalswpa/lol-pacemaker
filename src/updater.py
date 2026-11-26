"""Serverless model updater: GitHub Releases -> HTTPS + SHA-256 verified -> atomic swap.

Never touches the current model unless every file passed every check (AGENTS.md 11).
Any failure returns an `UpdateResult` and the app keeps running on its current/bundled model.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

RELEASES_URL = "https://api.github.com/repos/wpalswpa/lol-pacemaker/releases/latest"
MODEL_FILES = ("model_extended.onnx", "distribution_stats.json")
CHECKSUMS_FILE = "checksums.sha256"
MAX_BYTES = {"model_extended.onnx": 20_000_000, "distribution_stats.json": 5_000_000, CHECKSUMS_FILE: 100_000}
EXTENDED_INPUTS = 18
_SHA_LINE = re.compile(r"^([0-9a-fA-F]{64})\s+\*?([A-Za-z0-9._-]+)\s*$")
_VERSION = re.compile(r"^[A-Za-z0-9._-]{1,40}$")

Fetcher = Callable[[str, int], bytes]


def home_dir() -> Path:
    return Path(os.environ.get("LOL_PACEMAKER_HOME") or (Path.home() / ".lol-pacemaker"))


def models_dir() -> Path:
    return home_dir() / "models"


def current_model_dir() -> Path | None:
    """Return the verified model directory pointed to by `current.json`, else None."""
    try:
        ptr = json.loads((models_dir() / "current.json").read_text(encoding="utf-8"))
        version = str(ptr["version"])
        if not _VERSION.match(version):
            return None
        d = models_dir() / version
        return d if (d / "model_extended.onnx").is_file() else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def current_version() -> str | None:
    d = current_model_dir()
    return d.name if d else None


@dataclass(frozen=True)
class UpdateResult:
    status: str  # updated | up_to_date | rejected | failed
    version: str | None = None
    reason: str = ""


class _HttpsOnlyRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        if urllib.parse.urlparse(newurl).scheme != "https":
            raise urllib.error.URLError(f"refusing non-HTTPS redirect: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def https_fetch(url: str, max_bytes: int) -> bytes:
    """Default fetcher: HTTPS only (incl. redirects), size capped, 10s timeout."""
    if urllib.parse.urlparse(url).scheme != "https":
        raise ValueError(f"HTTPS required: {url}")
    opener = urllib.request.build_opener(_HttpsOnlyRedirect)
    req = urllib.request.Request(url, headers={"User-Agent": "lol-pacemaker-updater", "Accept": "application/octet-stream, application/json"})
    with opener.open(req, timeout=10.0) as resp:
        data = resp.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError(f"download exceeds {max_bytes} bytes: {url}")
    return data


def parse_checksums(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _SHA_LINE.match(line)
        if not m:
            raise ValueError(f"malformed checksum line: {line[:60]!r}")
        out[m.group(2)] = m.group(1).lower()
    return out


def _validate_onnx(path: Path) -> None:
    import onnxruntime as ort

    sess = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    shape = sess.get_inputs()[0].shape
    if len(shape) != 2 or shape[1] != EXTENDED_INPUTS:
        raise ValueError(f"model input shape {shape} != [N, {EXTENDED_INPUTS}]")


def _validate_stats(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("tiers"), dict):
        raise ValueError("distribution_stats.json has no 'tiers' object")


def check_for_update(
    fetch: Fetcher = https_fetch,
    releases_url: str = RELEASES_URL,
    validate_model: Callable[[Path], None] = _validate_onnx,
) -> UpdateResult:
    """Download, verify and atomically install the latest release models."""
    try:
        release = json.loads(fetch(releases_url, 1_000_000).decode("utf-8"))
        version = str(release.get("tag_name") or "")
        if not _VERSION.match(version):
            return UpdateResult("rejected", None, "invalid release tag")
        if version == current_version():
            return UpdateResult("up_to_date", version)

        assets = {a.get("name"): a.get("browser_download_url") for a in (release.get("assets") or []) if isinstance(a, dict)}
        wanted = (*MODEL_FILES, CHECKSUMS_FILE)
        missing = [n for n in wanted if not assets.get(n)]
        if missing:
            return UpdateResult("rejected", version, f"release is missing assets: {missing}")
        for name in wanted:
            if urllib.parse.urlparse(str(assets[name])).scheme != "https":
                return UpdateResult("rejected", version, f"non-HTTPS asset url for {name}")

        checksums = parse_checksums(fetch(str(assets[CHECKSUMS_FILE]), MAX_BYTES[CHECKSUMS_FILE]).decode("utf-8"))
        root = models_dir()
        root.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=".stage-", dir=root))
        try:
            for name in MODEL_FILES:
                expected = checksums.get(name)
                if not expected:
                    return UpdateResult("rejected", version, f"no checksum listed for {name}")
                blob = fetch(str(assets[name]), MAX_BYTES[name])
                if hashlib.sha256(blob).hexdigest() != expected:
                    return UpdateResult("rejected", version, f"SHA-256 mismatch for {name}")
                (stage / name).write_bytes(blob)
            validate_model(stage / "model_extended.onnx")
            _validate_stats(stage / "distribution_stats.json")

            final = root / version
            if final.exists():
                shutil.rmtree(final)
            os.replace(stage, final)  # stage dir becomes the version dir
            ptr_tmp = root / ".current.json.tmp"
            ptr_tmp.write_text(json.dumps({"version": version}), encoding="utf-8")
            os.replace(ptr_tmp, root / "current.json")  # atomic pointer swap
            return UpdateResult("updated", version)
        finally:
            shutil.rmtree(stage, ignore_errors=True)
    except Exception as exc:  # network error, bad JSON, bad ONNX ... -> keep current model
        return UpdateResult("failed", None, f"{type(exc).__name__}: {exc}")
