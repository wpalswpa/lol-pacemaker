// AGENTS §19: offline evaluation of the web review (worker/review.js) on real held-out matches.
// Version-agnostic: drives `handleReview` with a mocked Riot fetch, so the same checks run on any
// revision of review.js (pass --module). Rules for case categories are fixed here, before results.
//   node scripts/eval_review.mjs [--module worker/review.js] [--data data/store/matches.jsonl.gz]
import { readFileSync } from "node:fs";
import { gunzipSync } from "node:zlib";
import { fileURLToPath, pathToFileURL } from "node:url";
import { dirname, join, resolve } from "node:path";
import { performance } from "node:perf_hooks";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const arg = (k, d) => { const i = process.argv.indexOf(k); return i > 0 ? process.argv[i + 1] : d; };
const modPath = resolve(arg("--module", join(ROOT, "worker", "review.js")));
const dataPath = resolve(arg("--data", join(ROOT, "data", "store", "matches.jsonl.gz")));
const mod = await import(pathToFileURL(modPath).href);

const rows = gunzipSync(readFileSync(dataPath)).toString("utf-8").split("\n").filter(Boolean).map((l) => JSON.parse(l));
const isBaselineSplit = (tag) => parseInt(String(tag).slice(0, 8), 16) % 3 !== 0;
const LABEL = { lane: "라인전", farm: "파밍", combat: "교전", vision: "시야", survival: "생존", damage: "딜 기여" };

let cur = null; // { match, timeline, puuid }
globalThis.fetch = async (url) => {
  const u = String(url);
  const ok = (b) => new Response(JSON.stringify(b), { status: 200, headers: { "content-type": "application/json" } });
  if (u.includes("/accounts/by-riot-id/")) return ok({ puuid: cur.puuid, gameName: "eval", tagLine: "KR1" });
  if (u.endsWith("/timeline")) return cur.timeline ? ok(cur.timeline) : new Response("{}", { status: 404 });
  return ok(cur.match);
};

async function review(match, timeline, puuid) {
  cur = { match, timeline, puuid };
  const req = new Request("https://eval.local/v1/review", { method: "POST", body: JSON.stringify({ match_id: "KR_1", riot_id: "eval#KR1", lang: "ko" }) });
  const t0 = performance.now();
  try {
    const res = await mod.handleReview(req, { RIOT_API_KEY: "offline-eval" }, {});
    const body = await res.json();
    return { ms: performance.now() - t0, status: res.status, body };
  } catch (e) {
    return { ms: performance.now() - t0, status: 0, error: String(e && e.message || e) };
  }
}

// Independent case facts (computed here, not by the module under test)
function facts(match, timeline, pid) {
  const info = match.info;
  const me = info.participants.find((p) => p.participantId === pid);
  const opp = me.teamPosition ? info.participants.find((p) => p.teamId !== me.teamId && p.teamPosition === me.teamPosition) : null;
  const f15 = (timeline?.info?.frames || []).find((f) => f.timestamp >= 15 * 60000 - 5000);
  const gd15 = opp && f15 && f15.timestamp <= 15 * 60000 + 30000
    ? f15.participantFrames[String(pid)].totalGold - f15.participantFrames[String(opp.participantId)].totalGold : null;
  const obj = (t) => ["dragon", "baron", "riftHerald"].reduce((a, k) => a + (t?.objectives?.[k]?.kills || 0), 0);
  const myT = info.teams.find((t) => t.teamId === me.teamId);
  const enT = info.teams.find((t) => t.teamId !== me.teamId);
  return { me, gd15, durMin: info.gameDuration / 60, objLead: obj(myT) - obj(enT) };
}

const remake = (info) => info.gameDuration < 300 || info.participants.some((p) => p.gameEndedInEarlySurrender);
// AGENTS §28 clean-data rule, written here independently of the module under test (old season / AFK).
const seasonOf = (info) => parseInt(String(info.gameVersion || "0").split(".")[0], 10) || 0;
const afkCount = (tl) => {
  const frames = tl?.info?.frames || [];
  let n = 0;
  for (let pid = 1; pid <= 10; pid++) {
    const xs = frames.map((f) => Number(f?.participantFrames?.[String(pid)]?.xp || 0));
    let stall = 0;
    for (let i = 3; i + 1 < xs.length; i++) { stall = xs[i + 1] - xs[i] <= 5 ? stall + 1 : 0; if (stall >= 4) { n += 1; break; } }
  }
  return n;
};
const season = Math.max(0, ...rows.map((r) => seasonOf(r.match.info)));
const dirty = (r) => !remake(r.match.info) && (seasonOf(r.match.info) !== season || afkCount(r.timeline) > 0);
const afkRows = rows.filter((r) => !remake(r.match.info) && afkCount(r.timeline) > 0);
const evalRows = rows.filter((r) => remake(r.match.info) || (!isBaselineSplit(r.match.metadata.matchId) && !dirty(r)));
const baseRows = rows.filter((r) => !remake(r.match.info) && !dirty(r) && isBaselineSplit(r.match.metadata.matchId) && r.match.info.queueId === 420);
const supVis = baseRows.flatMap((r) => r.match.info.participants.filter((p) => p.teamPosition === "UTILITY").map((p) => p.visionScore / (r.match.info.gameDuration / 60))).sort((a, b) => a - b);
const supVisP75 = supVis[Math.floor(0.75 * supVis.length)] ?? Infinity;

const out = {
  module: modPath.replace(ROOT + "/", ""), cases: 0, errors: { crash: 0, remake_analyzed: 0, aram_rift_metrics: 0, short_game_gd15: 0, missing_timeline: 0, missing_opponent: 0, missing_overall: 0, afk_unflagged: 0 },
  contradictions: { metric_side: 0, tier_claim: 0, checked_lines: 0 }, examples: [],
  categories: {}, calibration: {}, latency_ms: {},
};
const cat = (k) => (out.categories[k] ||= { n: 0, pass: 0 });
const times = [];
const tByKey = {};
const startsWithLabel = (s, label) => s.startsWith(label) || s.includes(`'${label}'`);

function checkContradictions(body) {
  const ms = body.metrics || [];
  const c = body.coaching || {};
  for (const [side, lines] of [["strength", c.strengths || []], ["improvement", c.improvements || []]]) {
    for (const line of lines) {
      out.contradictions.checked_lines += 1;
      if (/동티어|tier average/.test(line)) { out.contradictions.tier_claim += 1; if (out.examples.length < 6) out.examples.push({ side, line }); }
      for (const m of ms) {
        const label = LABEL[m.key] || m.label;
        if (!startsWithLabel(line, label)) continue;
        const bad = side === "strength" ? m.t_score < 50 : m.t_score >= 50;
        if (bad) { out.contradictions.metric_side += 1; if (out.examples.length < 6) out.examples.push({ side, line, t: m.t_score }); }
      }
    }
  }
  if (/동티어|tier average/.test(c.summary || "")) out.contradictions.tier_claim += 1;
}

for (const r of evalRows) {
  const info = r.match.info;
  const isRemake = remake(info);
  const isAram = info.queueId === 450;
  for (const p of info.participants) {
    const res = await review(r.match, r.timeline, p.puuid);
    out.cases += 1;
    times.push(res.ms);
    if (res.status !== 200) { out.errors.crash += 1; if (out.examples.length < 6) out.examples.push({ crash: res.error || res.status }); continue; }
    const b = res.body;
    const ms = b.metrics || [];
    if (isRemake) { cat("E_remake").n += 1; if (ms.length) out.errors.remake_analyzed += 1; else cat("E_remake").pass += 1; continue; }
    if (isAram && (ms.some((m) => m.key === "lane" || m.key === "vision") || b.gold_diff_15m !== null)) out.errors.aram_rift_metrics += 1;
    checkContradictions(b);
    if (ms.length === 6 && !(b.overall_perf && typeof b.overall_perf.t_score === "number" && b.overall_perf.grade)) out.errors.missing_overall += 1;
    if (info.queueId === 420 && b.overall_perf) ((tByKey[`overall:${p.teamPosition}`] ||= [])).push(b.overall_perf.t_score);
    if (info.queueId === 420) for (const m of ms) ((tByKey[`${m.key}:${p.teamPosition}`] ||= [])).push(m.t_score);
    if (isAram) for (const m of ms) ((tByKey[`aram_${m.key}`] ||= [])).push(m.t_score);
    if (info.queueId !== 420) continue;
    const f = facts(r.match, r.timeline, p.participantId);
    const S = b.coaching?.strengths || [];
    const I = b.coaching?.improvements || [];
    if (f.gd15 !== null && f.gd15 >= 1000 && !p.win) { const c = cat("A_lane_lead_then_loss"); c.n += 1; if (I.some((s) => s.includes("분수령"))) c.pass += 1; }
    if (p.teamPosition === "UTILITY" && p.visionScore / f.durMin >= supVisP75) { const c = cat("B_vision_carry_support"); c.n += 1; if (S.some((s) => startsWithLabel(s, "시야"))) c.pass += 1; }
    if (p.deaths >= 5 && p.kills + p.assists <= 3) { const c = cat("C_collapse_deaths"); c.n += 1; if (I.some((s) => startsWithLabel(s, "생존")) && !S.some((s) => startsWithLabel(s, "생존"))) c.pass += 1; }
    if (p.teamPosition === "JUNGLE" && p.win && f.objLead >= 3) { const c = cat("D_jungle_objective_win"); c.n += 1; if (S.some((s) => /분수령|오브젝트|바론/.test(s))) c.pass += 1; }
  }
}

// Edge variants built from real eval matches (first 10 ranked, participant 1)
const ranked = evalRows.filter((r) => r.match.info.queueId === 420 && !remake(r.match.info)).slice(0, 10);
for (const r of ranked) {
  const p = r.match.info.participants[0];
  let res = await review(r.match, null, p.puuid);
  if (res.status !== 200 || res.body.gold_diff_15m !== null) out.errors.missing_timeline += 1;
  const short = structuredClone(r);
  short.match.info.gameDuration = 12 * 60;
  short.timeline.info.frames = short.timeline.info.frames.slice(0, 13);
  res = await review(short.match, short.timeline, p.puuid);
  if (res.status !== 200 || res.body.gold_diff_15m !== null) out.errors.short_game_gd15 += 1;
  const noOpp = structuredClone(r);
  noOpp.match.info.participants.forEach((q) => { if (q.teamId !== p.teamId && q.teamPosition === p.teamPosition) q.teamPosition = ""; });
  res = await review(noOpp.match, noOpp.timeline, p.puuid);
  if (res.status !== 200 || (res.body.metrics || []).length !== 6) out.errors.missing_opponent += 1;
  out.cases += 3;
}

// AFK games (any split): a review of such a game must say so (AGENTS §28 data_quality).
for (const r of afkRows) {
  const res = await review(r.match, r.timeline, r.match.info.participants[0].puuid);
  if (res.status !== 200 || !(res.body.data_quality && res.body.data_quality.afk > 0)) out.errors.afk_unflagged += 1;
  out.cases += 1;
}

for (const [k, ts] of Object.entries(tByKey)) {
  const mean = ts.reduce((a, b) => a + b, 0) / ts.length;
  out.calibration[k] = { n: ts.length, mean_t: Number(mean.toFixed(1)), extreme_share: Number((ts.filter((t) => Math.abs(t - 50) > 20).length / ts.length).toFixed(3)) };
}
const cal = Object.entries(out.calibration).filter(([k, v]) => !k.startsWith("aram") && v.n >= 10);
out.calibration_summary = {
  cells: cal.length,
  mean_abs_t_bias: Number((cal.reduce((a, [, v]) => a + Math.abs(v.mean_t - 50), 0) / Math.max(1, cal.length)).toFixed(2)),
  extreme_share: Number((cal.reduce((a, [, v]) => a + v.extreme_share * v.n, 0) / Math.max(1, cal.reduce((a, [, v]) => a + v.n, 0))).toFixed(3)),
  expected_extreme_share_normal: 0.046,
};
times.sort((a, b) => a - b);
out.latency_ms = { n: times.length, p50: Number(times[Math.floor(times.length * 0.5)].toFixed(3)), p95: Number(times[Math.floor(times.length * 0.95)].toFixed(3)), max: Number(times[times.length - 1].toFixed(3)) };
if (typeof mod.analyzeMatch === "function" && mod.REVIEW_RULES) {
  const { REVIEW_BASELINE } = await import(pathToFileURL(join(dirname(modPath), "review_baseline.js")).href);
  const pure = [];
  for (const r of evalRows) for (const p of r.match.info.participants) {
    const t0 = performance.now();
    mod.analyzeMatch(r.match, r.timeline, p.puuid, "ko", REVIEW_BASELINE);
    pure.push(performance.now() - t0);
  }
  pure.sort((a, b) => a - b);
  out.latency_ms.analyze_p95 = Number(pure[Math.floor(pure.length * 0.95)].toFixed(3));
}
out.eval_matches = { season, afk_games: afkRows.length, excluded_dirty: rows.filter(dirty).length, total: evalRows.length, remake: evalRows.filter((r) => remake(r.match.info)).length, aram: evalRows.filter((r) => r.match.info.queueId === 450 && !remake(r.match.info)).length, ranked: evalRows.filter((r) => r.match.info.queueId === 420 && !remake(r.match.info)).length };
console.log(JSON.stringify(out, null, 1));
