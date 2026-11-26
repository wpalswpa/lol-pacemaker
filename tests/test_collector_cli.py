import io
import json
import urllib.error
from pathlib import Path

import pytest

from ml import collector
from ml.collector import Collector, FatalApiError, RiotRateLimitedClient, extract_player_stats, load_jsonl
from tests.synth import sample_match, sample_timeline


def http_error(code: int, retry_after: str | None = None) -> urllib.error.HTTPError:
    hdrs = {"Retry-After": retry_after} if retry_after else {}
    return urllib.error.HTTPError("https://x", code, "err", hdrs, io.BytesIO(b""))  # type: ignore[arg-type]


class Resp:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


def make_client(script, **kw):
    """Client whose opener replays `script` (values or exceptions); records sleeps and request headers."""
    sleeps, seen = [], []
    it = iter(script)

    def opener(req, timeout=None):
        seen.append(req)
        item = next(it)
        if isinstance(item, Exception):
            raise item
        return Resp(item)

    return RiotRateLimitedClient("RGAPI-SECRET-KEY", sleep=sleeps.append, opener=opener, min_interval=0.0, **kw), sleeps, seen


def test_client_backs_off_on_429_and_5xx_then_succeeds():
    client, sleeps, _ = make_client([http_error(429, "7"), http_error(503), http_error(500), {"ok": 1}])
    assert client.get_json("https://kr.api.riotgames.com/x") == {"ok": 1}
    assert sleeps == [7.0, 2.0 ** 1, 2.0 ** 2]  # Retry-After honoured, then exponential 5xx backoff


def test_client_gives_up_after_max_retries_and_hides_key_on_auth_failure():
    client, _, seen = make_client([http_error(429, "1")] * 3, max_retries=2)
    with pytest.raises(urllib.error.HTTPError):
        client.get_json("https://kr.api.riotgames.com/x")
    client2, _, seen2 = make_client([http_error(403)])
    with pytest.raises(FatalApiError) as exc:
        client2.get_json("https://kr.api.riotgames.com/x")
    assert "RGAPI-SECRET-KEY" not in str(exc.value)
    assert all("RGAPI" not in r.full_url for r in seen + seen2)  # key travels only in a header
    assert seen2[0].get_header("X-riot-token") == "RGAPI-SECRET-KEY"


def test_client_throttles_between_calls():
    sleeps, calls = [], iter([{"a": 1}, {"b": 2}])
    client = RiotRateLimitedClient("k", sleep=sleeps.append, opener=lambda r, timeout=None: Resp(next(calls)), min_interval=5.0)
    client.get_json("https://x/1")
    client.get_json("https://x/2")
    assert len(sleeps) >= 1 and 0 < sleeps[-1] <= 5.0


class FakeRiot:
    """Fake LEAGUE-V4 + MATCH-V5 server keyed by URL fragments."""

    def __init__(self, n_matches=6, remake_ids=(), early_surrender_ids=(), old_patch_ids=()):
        self.calls: list[str] = []
        self.matches = {}
        for i in range(n_matches):
            mid = f"KR_{i}"
            dur = 200 if mid in remake_ids else 1500
            m = sample_match(mid, dur, i % 2 == 0, version="15.1.1" if mid in old_patch_ids else "16.5.1")
            if mid in early_surrender_ids:
                m["info"]["participants"][0]["gameEndedInEarlySurrender"] = True
            self.matches[mid] = m

    def get_json(self, url: str):
        self.calls.append(url)
        if "/league/v4/entries/" in url:
            return [{"puuid": f"PU{i}"} for i in range(6)] + [{"summonerId": "no-puuid"}]
        if "/league/v4/" in url:
            return {"entries": [{"puuid": "APEX1"}]}
        if "/by-puuid/" in url:
            return list(self.matches)
        mid = url.split("/matches/")[1].split("/")[0]
        if mid not in self.matches:
            raise http_error(404)
        if url.endswith("/timeline"):
            return sample_timeline(int(mid.split("_")[1]) % 2 == 0)
        return self.matches[mid]


def make_collector(tmp_path: Path, fake, bucket="low", **kw):
    return Collector(fake, "kr", bucket, tmp_path / "out.jsonl", tmp_path / "ckpt.json", log=lambda m: None, **kw)


def test_collect_writes_jsonl_with_both_row_kinds_and_filters(tmp_path):
    fake = FakeRiot(remake_ids={"KR_1"}, early_surrender_ids={"KR_2"}, old_patch_ids={"KR_3"})
    col = make_collector(tmp_path, fake, patch="16.5")
    n = col.collect(max_matches=50, per_tier=1)
    snaps, players = load_jsonl([tmp_path / "out.jsonl"])
    ids = {r["match_id"] for r in snaps}
    assert n == len(ids) == 3 and ids == {"KR_0", "KR_4", "KR_5"}   # remake, early surrender, other patch dropped
    assert snaps and players and {r["tier_bucket"] for r in snaps} == {0.0}  # low bucket
    assert all(r["patch"] == "16.5" for r in snaps)
    assert {"cs_per_min", "level", "kill_participation", "role", "minute"} <= set(players[0])


def test_checkpoint_resume_does_not_duplicate_and_continues(tmp_path):
    fake = FakeRiot(n_matches=6)
    first = make_collector(tmp_path, fake).collect(max_matches=2, per_tier=1)
    assert first == 2
    lines_after_first = (tmp_path / "out.jsonl").read_text().splitlines()
    calls_before = len(fake.calls)

    resumed = make_collector(tmp_path, FakeRiot(n_matches=6))  # new process, same files
    assert resumed.state["written_matches"] == 2 and len(resumed.state["done_matches"]) == 2
    second = resumed.collect(max_matches=2, per_tier=1)
    assert second == 2
    snaps, _ = load_jsonl([tmp_path / "out.jsonl"])
    ids = [r["match_id"] for r in snaps if r["game_time_min"] == 10.0]
    assert len(ids) == len(set(ids)) == 4                       # no duplicates across the two runs
    assert (tmp_path / "out.jsonl").read_text().splitlines()[: len(lines_after_first)] == lines_after_first  # append-only
    assert calls_before > 0


def test_apex_bucket_uses_by_queue_endpoints_and_skips_entries_without_puuid(tmp_path):
    fake = FakeRiot()
    col = make_collector(tmp_path, fake, bucket="high")
    puuids = col.sample_puuids(per_tier=3)
    assert "APEX1" in puuids and any("masterleagues/by-queue" in c for c in fake.calls)
    assert all(isinstance(p, str) for p in puuids)
    with pytest.raises(ValueError):
        make_collector(tmp_path, fake, bucket="nope")


def test_missing_timeline_is_skipped_not_fatal(tmp_path):
    fake = FakeRiot(n_matches=2)
    orig = fake.get_json
    fake.get_json = lambda url: (_ for _ in ()).throw(http_error(404)) if url.endswith("/timeline") else orig(url)  # type: ignore[assignment]
    assert make_collector(tmp_path, fake).collect(5, per_tier=1) == 0


def test_player_stats_values():
    rows = extract_player_stats(sample_match("KR_9", 1500, True), sample_timeline(True), "mid", minutes=(10,))
    assert len(rows) == 10 and {r["minute"] for r in rows} == {10}
    blue = next(r for r in rows if r["role"] == "TOP")
    assert blue["cs_per_min"] == (10 * 7 + 15) / 10 and blue["level"] == 6.0
    assert extract_player_stats(sample_match("KR_9", 200, True), sample_timeline(True)) == []


def test_cli_requires_key_and_never_prints_it(monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("RIOT_API_KEY", raising=False)
    assert collector.main(["--bucket", "low", "--out", str(tmp_path / "o"), "--checkpoint", str(tmp_path / "c")]) == 2
    monkeypatch.setenv("RIOT_API_KEY", "RGAPI-SECRET-KEY")
    monkeypatch.setattr(collector.Collector, "collect", lambda self, *a, **k: (_ for _ in ()).throw(FatalApiError("rejected (HTTP 403)")))
    rc = collector.main(["--bucket", "low", "--out", str(tmp_path / "o"), "--checkpoint", str(tmp_path / "c")])
    cap = capsys.readouterr()
    assert rc == 3 and "RGAPI-SECRET-KEY" not in cap.out + cap.err
