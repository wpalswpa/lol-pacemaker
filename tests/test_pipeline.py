"""AGENTS §20: automatic collection pipeline (no network: fake Riot client, temp local store)."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from ml import pipeline
from ml.collector import FatalApiError
from tests.synth import sample_match, sample_timeline

ROOT = Path(__file__).resolve().parent.parent


def _named(match_id: str, queue: int = 420, duration: int = 1500) -> dict:
    m = sample_match(match_id, duration, True)
    m["info"]["queueId"] = queue
    for p in m["info"]["participants"]:
        p.update(puuid="REAL-PUUID-" + str(p["participantId"]), riotIdGameName="Someone", riotIdTagline="KR1", summonerName="Someone")
    return m


class FakeClient:
    def __init__(self, fail_on: str | None = None) -> None:
        self.calls: list[str] = []
        self.fail_on = fail_on
        self.n = 0

    def get_json(self, url: str):
        self.calls.append(url)
        if self.fail_on and self.fail_on in url:
            raise FatalApiError("Riot API rejected the key (HTTP 403)")
        if "/league/v4/" in url:
            entries = [{"puuid": f"pu{i}"} for i in range(5)]
            return {"entries": entries} if "leagues/by-queue" in url else entries  # apex tiers return an object
        if "/ids?" in url:
            q = 450 if "queue=450" in url else 420
            self.n += 1
            return [f"KR_{q}_{self.n}_{i}" for i in range(3)]
        if url.endswith("/timeline"):
            return sample_timeline(True)
        mid = url.rsplit("/", 1)[1]
        return _named(mid, queue=int(mid.split("_")[1]))


def test_slim_drops_identity_and_tags_match():
    row = pipeline.slim(_named("KR_123"), sample_timeline(True), "mid")
    blob = json.dumps(row)
    assert "REAL-PUUID" not in blob and "Someone" not in blob and "KR_123" not in blob
    assert row["match"]["metadata"]["matchId"] == pipeline.tag("KR_123")
    assert {p["puuid"] for p in row["match"]["info"]["participants"]} == {f"p{i}" for i in range(1, 11)}
    assert pipeline.decode_payload(pipeline.encode_payload(row)) == row
    assert pipeline.slim(row["match"], row["timeline"], "mid")["match"]["metadata"]["matchId"] == row["match"]["metadata"]["matchId"]


def test_local_store_ignores_duplicates(tmp_path):
    store = pipeline.LocalStore(tmp_path / "m.jsonl.gz")
    rows = [pipeline.slim(_named(f"KR_{i}"), None, "low") for i in range(3)]
    assert store.add(rows) == 3 and store.add(rows) == 0 and len(store.rows()) == 3


def test_collect_respects_caps_and_skips_known(tmp_path):
    store = pipeline.LocalStore(tmp_path / "m.jsonl.gz")
    c = pipeline.collect(FakeClient(), store, per_bucket=1, aram=1, max_total=3, seed=1, log=lambda m: None)
    assert c["added"] == 3 and len(store.rows()) == 3
    assert all(v <= 1 for v in c["ranked"].values()) and c["aram"] <= 1
    again = pipeline.collect(FakeClient(), store, per_bucket=1, aram=1, max_total=3, seed=1, log=lambda m: None)
    assert again["skipped_known"] > 0 and len({r["match"]["metadata"]["matchId"] for r in store.rows()}) == len(store.rows())


def test_collect_stops_on_rejected_key(tmp_path):
    with pytest.raises(FatalApiError):
        pipeline.collect(FakeClient(fail_on="/ids?"), pipeline.LocalStore(tmp_path / "m.jsonl.gz"), max_total=3, seed=1, log=lambda m: None)


def test_eval_split_is_stable():
    tags = [pipeline.tag(f"KR_{i}") for i in range(300)]
    share = sum(pipeline.is_eval_tag(t) for t in tags) / len(tags)
    assert 0.25 < share < 0.42 and pipeline.is_eval_tag(tags[0]) == pipeline.is_eval_tag(tags[0])


def test_worker_rejects_unanonymized_or_oversized_rows():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    rec = pipeline.store_record(pipeline.slim(_named("KR_9"), sample_timeline(True), "high"), "kr")
    script = f"""
import {{ validateMatch }} from "./worker/matches.js";
const ok = {json.dumps(rec)};
console.log(JSON.stringify([validateMatch(ok), validateMatch({{ ...ok, tag: "KR_9" }}),
  validateMatch({{ ...ok, payload: "x".repeat(70000) }}), validateMatch({{ ...ok, bucket: "gold" }})]));
"""
    res = subprocess.run([node, "--input-type=module", "-e", script], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout) == [None, "bad_tag", "bad_payload", "bad_bucket"]
