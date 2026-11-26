/**
 * AGENTS §20: admin-only store for the automatic collection pipeline (ml/pipeline.py).
 * POST /v1/admin/matches  {matches: [{tag, platform, queue, bucket, patch, duration, remake, payload}]} (<= 20)
 * GET  /v1/admin/matches?after=<id>&limit=<=50>&fields=tag|all
 * Both need `Authorization: Bearer <ADMIN_TOKEN>`. Rows are already anonymized by the pipeline
 * (no names, Riot IDs or PUUIDs; tag = SHA-256 prefix of the match id). Duplicate tags are ignored.
 */
function json(body, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

const MAX_BATCH = 20;
const MAX_PAYLOAD = 64 * 1024;
const TAG_RE = /^[0-9a-f]{12}$/;
const PAYLOAD_RE = /^[A-Za-z0-9+/=]+$/;

export function validateMatch(m) {
  if (!m || typeof m !== "object") return "not_object";
  if (!TAG_RE.test(String(m.tag || ""))) return "bad_tag";
  if (!/^[a-z0-9]{2,5}$/.test(String(m.platform || ""))) return "bad_platform";
  if (![420, 450].includes(m.queue) && !(Number.isInteger(m.queue) && m.remake === 1)) return "bad_queue";
  if (!["low", "mid", "high"].includes(m.bucket)) return "bad_bucket";
  if (!/^\d{1,2}\.\d{1,2}$/.test(String(m.patch || ""))) return "bad_patch";
  if (!Number.isInteger(m.duration) || m.duration < 0 || m.duration > 7200) return "bad_duration";
  if (m.remake !== 0 && m.remake !== 1) return "bad_remake";
  const p = String(m.payload || "");
  if (!p || p.length > MAX_PAYLOAD || !PAYLOAD_RE.test(p)) return "bad_payload";
  return null;
}

// Constant-time token check (SHA-256 both sides so lengths match; Workers provide timingSafeEqual).
async function sameSecret(given, expected) {
  const enc = new TextEncoder();
  const [a, b] = await Promise.all([given, expected].map((v) => crypto.subtle.digest("SHA-256", enc.encode(v))));
  if (crypto.subtle.timingSafeEqual) return crypto.subtle.timingSafeEqual(a, b);
  const x = new Uint8Array(a), y = new Uint8Array(b);
  let diff = 0;
  for (let i = 0; i < x.length; i++) diff |= x[i] ^ y[i];
  return diff === 0;
}

export async function handleAdminMatches(request, env) {
  const auth = request.headers.get("Authorization") || "";
  if (!env.ADMIN_TOKEN || !(await sameSecret(auth, `Bearer ${env.ADMIN_TOKEN}`))) return json({ error: "forbidden" }, 403);
  if (!env.DB) return json({ error: "service_unavailable" }, 503);

  if (request.method === "GET") {
    const url = new URL(request.url);
    const after = Math.max(0, Number.parseInt(url.searchParams.get("after") || "0", 10) || 0);
    const limit = Math.min(50, Math.max(1, Number.parseInt(url.searchParams.get("limit") || "50", 10) || 50));
    const cols = url.searchParams.get("fields") === "tag" ? "id, tag" : "id, tag, day, platform, queue, bucket, patch, duration, remake, payload";
    const { results } = await env.DB.prepare(`SELECT ${cols} FROM riot_matches WHERE id > ? ORDER BY id LIMIT ?`).bind(after, limit).all();
    return json({ items: results || [] });
  }
  if (request.method !== "POST") return json({ error: "method_not_allowed" }, 405);

  let body;
  try { body = await request.json(); } catch { return json({ error: "invalid_json" }, 400); }
  const list = Array.isArray(body?.matches) ? body.matches : null;
  if (!list || !list.length || list.length > MAX_BATCH) return json({ error: "bad_batch" }, 400);
  for (const m of list) {
    const err = validateMatch(m);
    if (err) return json({ error: err }, 400);
  }
  const day = new Date().toISOString().slice(0, 10);
  const stmt = env.DB.prepare(
    "INSERT OR IGNORE INTO riot_matches (tag, day, platform, queue, bucket, patch, duration, remake, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
  );
  const res = await env.DB.batch(list.map((m) => stmt.bind(m.tag, day, m.platform, m.queue, m.bucket, m.patch, m.duration, m.remake, m.payload)));
  const inserted = res.reduce((a, r) => a + (r.meta?.changes || 0), 0);
  return json({ inserted, ignored: list.length - inserted });
}
