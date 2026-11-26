/**
 * AGENTS §14: opt-in anonymous match numbers -> Cloudflare D1 (binding `DB`).
 * POST   /v1/games                 store one game (strict schema, 8 KB, 30 games / install / day)
 * DELETE /v1/games?install_id=...  withdrawal: delete every row of that install id
 * Never stores IP, user agent, names, or time of day (date only).
 */

export const FEATURES = [
  "game_time_min", "first_blood", "kills_diff", "assists_diff", "cs_diff", "avg_level_diff",
  "dragons_diff", "heralds_diff", "towers_diff", "voidgrubs_diff", "barons_diff", "elder_diff",
  "inhibitors_diff", "item_gold_diff", "recent_3m_kill_diff", "lane_cs_diff", "alive_diff", "tier_bucket",
];
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const MINUTES = new Set([10, 15, 20, 25]);
const MAX_BYTES = 8192;
const DAILY_CAP = 30;

/** Returns a clean row or an error string. Pure: unit-testable without D1. */
export function validateGame(body) {
  if (!body || typeof body !== "object" || Array.isArray(body)) return "bad_body";
  const keys = Object.keys(body).sort().join(",");
  if (keys !== "app_version,blue_win,install_id,lang,snapshots,tier") return "bad_keys";
  if (!UUID.test(body.install_id)) return "bad_install_id";
  if (typeof body.app_version !== "string" || !/^\d+\.\d+\.\d+$/.test(body.app_version)) return "bad_version";
  if (!["en", "ko", "ja", "es", "pt"].includes(body.lang)) return "bad_lang";
  if (!["low", "mid", "high"].includes(body.tier)) return "bad_tier";
  if (body.blue_win !== 0 && body.blue_win !== 1) return "bad_label";
  const snaps = body.snapshots;
  if (!Array.isArray(snaps) || snaps.length < 1 || snaps.length > 4) return "bad_snapshots";
  const seen = new Set();
  const clean = [];
  for (const s of snaps) {
    if (!s || !MINUTES.has(s.minute) || seen.has(s.minute)) return "bad_minute";
    seen.add(s.minute);
    const f = s.features;
    if (!f || typeof f !== "object" || Object.keys(f).length !== FEATURES.length) return "bad_features";
    const row = {};
    for (const k of FEATURES) {
      const v = f[k];
      if (typeof v !== "number" || !Number.isFinite(v) || Math.abs(v) > 100000) return "bad_value";
      row[k] = v;
    }
    clean.push({ minute: s.minute, features: row });
  }
  return { install_id: body.install_id, app_version: body.app_version, lang: body.lang, tier: body.tier, blue_win: body.blue_win, snapshots: clean };
}

const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json; charset=utf-8", "X-Content-Type-Options": "nosniff" } });

export async function handleGames(request, env) {
  if (!env.DB) return json({ error: "db_not_configured" }, 503);
  const url = new URL(request.url);

  if (request.method === "DELETE") {
    const id = url.searchParams.get("install_id") || "";
    if (!UUID.test(id)) return json({ error: "bad_install_id" }, 400);
    const r = await env.DB.prepare("DELETE FROM games WHERE install_id = ?").bind(id).run();
    return json({ ok: true, deleted: r.meta?.changes ?? 0 });
  }
  if (request.method !== "POST") return json({ error: "method_not_allowed" }, 405);

  const text = await request.text();
  if (text.length > MAX_BYTES) return json({ error: "too_large" }, 413);
  let body;
  try { body = JSON.parse(text); } catch { return json({ error: "bad_json" }, 400); }
  const row = validateGame(body);
  if (typeof row === "string") return json({ error: row }, 400);

  const day = new Date().toISOString().slice(0, 10);
  const count = await env.DB.prepare("SELECT COUNT(*) AS n FROM games WHERE install_id = ? AND day = ?").bind(row.install_id, day).first();
  if ((count?.n ?? 0) >= DAILY_CAP) return json({ error: "daily_cap" }, 429);
  await env.DB.prepare(
    "INSERT INTO games (day, install_id, app_version, lang, tier, blue_win, snapshots_json) VALUES (?, ?, ?, ?, ?, ?, ?)"
  ).bind(day, row.install_id, row.app_version, row.lang, row.tier, row.blue_win, JSON.stringify(row.snapshots)).run();
  return json({ ok: true });
}
