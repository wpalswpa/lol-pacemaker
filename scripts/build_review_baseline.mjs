// AGENTS §19·§20: build worker/review_baseline.js from real matches in the pipeline store (data/store, gitignored).
// Uses the exact `playerMetrics` the Worker uses, so baseline and review share one definition.
// Split: matches whose tag hash % 3 != 0 build the baseline; % 3 == 0 are kept for evaluation.
//   node scripts/build_review_baseline.mjs [data/store/matches.jsonl.gz] [--out worker/review_baseline.js] [--module worker/review.js]
import { readFileSync, writeFileSync } from "node:fs";
import { gunzipSync } from "node:zlib";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const argOf = (k, d) => { const i = process.argv.indexOf(k); return i > 0 ? process.argv[i + 1] : d; };
const { playerMetrics, queueGroup, isRemake, analyzeMatch, afkParticipants, seasonOf } = await import(argOf("--module", join(ROOT, "worker", "review.js")));

export const METRICS = [
  "cs_per_min", "gold_per_min", "kp", "vision_per_min", "deaths_per10", "dmg_share", "taken_share", "lane_gd15",
  "lvl6_lead_sec", "gank_deaths", "obj_vision_rate", "power_play_rate", "first_death_fights", "max_drop_3m", "max_rise_3m",
];
export const MIN_N = 30;

export function loadRows(path) {
  return gunzipSync(readFileSync(path)).toString("utf-8").split("\n").filter(Boolean).map((l) => JSON.parse(l));
}
export const isBaselineSplit = (tag) => parseInt(String(tag).slice(0, 8), 16) % 3 !== 0;

function stats(vals) {
  const n = vals.length;
  if (n < 2) return null;
  const mean = vals.reduce((a, b) => a + b, 0) / n;
  const std = Math.sqrt(vals.reduce((a, b) => a + (b - mean) ** 2, 0) / (n - 1));
  return { mean: Number(mean.toFixed(4)), std: Number(std.toFixed(4)), n };
}

// AGENTS §28: the same clean-data rule as ml/pipeline.py `clean_rows` (remake / old season / AFK).
export function cleanFilter(rows) {
  const season = Math.max(0, ...rows.map((r) => seasonOf ? seasonOf(r.match.info) : 0));
  const dropped = { remake: 0, old_season: 0, afk: 0 };
  const keep = rows.filter((r) => {
    const bad = [];
    if (isRemake(r.match.info)) bad.push("remake");
    if (seasonOf && season && seasonOf(r.match.info) !== season) bad.push("old_season");
    if (afkParticipants && afkParticipants(r.timeline).length) bad.push("afk");
    bad.forEach((k) => { dropped[k] += 1; });
    return !bad.length;
  });
  return { keep, quality: { season, kept: keep.length, ...dropped } };
}
const tertiles = (xs) => {
  const s = [...xs].sort((a, b) => a - b);
  const q = (p) => s[Math.min(s.length - 1, Math.floor(p * s.length))];
  return [Math.round(q(1 / 3) * 2) / 2, Math.round(q(2 / 3) * 2) / 2];
};

export function buildBaseline(allRows) {
  const { keep: rows, quality } = cleanFilter(allRows);
  const players = { rift: [], aram: [] };
  const durations = { rift: [], aram: [] };
  let matches = { rift: 0, aram: 0 };
  for (const r of rows) {
    const info = r.match.info;
    if (!isBaselineSplit(r.match.metadata.matchId) || isRemake(info) || !r.timeline) continue;
    const g = Number(info.queueId) === 420 ? "rift" : queueGroup(info) === "aram" ? "aram" : null;
    if (!g) continue;
    matches[g] += 1;
    durations[g].push(info.gameDuration / 60);
    for (const p of info.participants) {
      const pm = playerMetrics(r.match, r.timeline, p.participantId);
      if (pm) players[g].push({ ...pm.m, ...pm.pat });
    }
  }
  const cells = {};
  const edges = {};
  for (const g of ["rift", "aram"]) {
    if (!durations[g].length) continue;
    edges[g] = tertiles(durations[g]);
    const bucketOf = (d) => (d < edges[g][0] ? "short" : d < edges[g][1] ? "mid" : "long");
    cells[g] = {};
    const positions = g === "aram" ? ["ALL"] : ["ALL", "TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"];
    for (const pos of positions) {
      cells[g][pos] = {};
      for (const b of ["ALL", "short", "mid", "long"]) {
        const sel = players[g].filter((p) => (pos === "ALL" || p.position === pos) && (b === "ALL" || bucketOf(p.durMin) === b));
        const cell = {};
        for (const k of METRICS) {
          const st = stats(sel.map((p) => p[k]).filter((v) => typeof v === "number" && Number.isFinite(v)));
          if (st && st.n >= MIN_N) cell[k] = st;
        }
        if (Object.keys(cell).length) cells[g][pos][b] = cell;
      }
    }
  }
  // Second pass: the overall score is the mean z of the six metrics; store its own distribution
  // so the review can re-standardize it (a mean of correlated z-scores has std < 1).
  const first = { schema: 1, min_n: MIN_N, duration_edges: edges, cells };
  const comp = { rift: [], aram: [] };
  for (const r of rows) {
    const info = r.match.info;
    if (!isBaselineSplit(r.match.metadata.matchId) || isRemake(info) || !r.timeline) continue;
    const g = Number(info.queueId) === 420 ? "rift" : queueGroup(info) === "aram" ? "aram" : null;
    if (!g) continue;
    for (const p of info.participants) {
      const out = analyzeMatch(r.match, r.timeline, p.puuid, "en", first);
      const zs = (out.metrics || []).filter((m) => m.z !== null).map((m) => m.z);
      if (zs.length) comp[g].push({ position: out.baseline.position, bucket: out.baseline.duration_bucket, v: zs.reduce((a, b) => a + b, 0) / zs.length });
    }
  }
  for (const g of Object.keys(cells)) {
    for (const pos of Object.keys(cells[g])) {
      for (const b of Object.keys(cells[g][pos])) {
        const st = stats(comp[g].filter((c) => (pos === "ALL" || c.position === pos) && (b === "ALL" || c.bucket === b)).map((c) => c.v));
        if (st && st.n >= MIN_N) cells[g][pos][b].composite_z = st;
      }
    }
  }
  return {
    schema: 1, min_n: MIN_N, duration_edges: edges, cells,
    source: { platform: "KR", queues: { rift: 420, aram: 450 }, matches, players: { rift: players.rift.length, aram: players.aram.length },
      split: "sha256(matchId) tag % 3 != 0", quality, built: new Date().toISOString().slice(0, 10) },
  };
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const path = process.argv[2] && !process.argv[2].startsWith("--") ? process.argv[2] : join(ROOT, "data", "store", "matches.jsonl.gz");
  const bl = buildBaseline(loadRows(path));
  const out = argOf("--out", join(ROOT, "worker", "review_baseline.js"));
  writeFileSync(out, `// Generated by scripts/build_review_baseline.mjs (AGENTS §19). Aggregates only: no names, ids or matches.\n`
    + `export const REVIEW_BASELINE = ${JSON.stringify(bl, null, 1)};\n`);
  console.log(JSON.stringify(bl.source));
}
