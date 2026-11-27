/**
 * AGENTS §16: in-site contact -> operator (Cloudflare D1, binding `DB`).
 * POST /v1/support   {category, message, contact?, lang}   from https://wpalswpa.github.io (CORS)
 * GET  /v1/support   operator only: `Authorization: Bearer <ADMIN_TOKEN>` (Worker secret)
 * No raw IP is stored: a SHA-256 of (IP + today + SUPPORT_SALT) only rate-limits 5 messages / hour.
 */

import { isAdminSession, verifyToken } from "./review.js";

const CATEGORIES = new Set(["install", "error", "idea", "delete"]);
const LANGS = new Set(["en", "ko", "ja", "es", "pt"]);
const PER_HOUR = 5;

async function sha256(text) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].map(b => b.toString(16).padStart(2, "0")).join("");
}

/** Pure validation, returns a clean ticket or an error string. */
export function validateTicket(body) {
  if (!body || typeof body !== "object" || Array.isArray(body)) return "bad_body";
  const category = String(body.category || "");
  const message = typeof body.message === "string" ? body.message.trim() : "";
  const contact = typeof body.contact === "string" ? body.contact.trim() : "";
  const lang = LANGS.has(body.lang) ? body.lang : "en";
  if (!CATEGORIES.has(category)) return "bad_category";
  if (!message || message.length > 2000) return "bad_message";
  if (contact.length > 120) return "bad_contact";
  return { category, message, contact, lang };
}

export async function handleSupport(request, env, headers) {
  const json = (obj, status = 200) => new Response(JSON.stringify(obj), { status, headers });
  if (!env.DB) return json({ error: "db_not_configured" }, 503);

  const isOperator = async () => {
    const auth = request.headers.get("Authorization") || "";
    if (env.ADMIN_TOKEN && auth === `Bearer ${env.ADMIN_TOKEN}`) return true;
    const session = await verifyToken(request, env);
    return isAdminSession(session, env);
  };

  if (request.method === "GET") {
    if (!(await isOperator())) return json({ error: "forbidden" }, 403);
    const rows = await env.DB.prepare(
      "SELECT id, day, category, lang, message, contact FROM tickets ORDER BY id DESC LIMIT 100"
    ).all();
    return json({ tickets: rows.results || [] });
  }
  if (request.method === "DELETE") {
    if (!(await isOperator())) return json({ error: "forbidden" }, 403);
    const id = Number(new URL(request.url).searchParams.get("id") || "0");
    if (!Number.isInteger(id) || id <= 0) return json({ error: "invalid_id" }, 400);
    await env.DB.prepare("DELETE FROM tickets WHERE id = ?").bind(id).run();
    return json({ ok: true, deleted: id });
  }
  if (request.method !== "POST") return json({ error: "method_not_allowed" }, 405);

  const session = await verifyToken(request, env);
  if (!session) return json({ error: "auth_required" }, 401);

  const text = await request.text();
  if (text.length > 6000) return json({ error: "too_large" }, 413);
  let body;
  try { body = JSON.parse(text); } catch { return json({ error: "bad_json" }, 400); }
  const t = validateTicket(body);
  if (typeof t === "string") return json({ error: t }, 400);

  const now = new Date();
  const day = now.toISOString().slice(0, 10);
  const hour = now.toISOString().slice(0, 13);
  const ip = request.headers.get("CF-Connecting-IP") || "unknown";
  const ipHash = await sha256(`${ip}|${day}|${env.SUPPORT_SALT || "pacemaker"}`);
  const recent = await env.DB.prepare("SELECT COUNT(*) AS n FROM tickets WHERE ip_hash = ? AND hour = ?").bind(ipHash, hour).first();
  if ((recent?.n ?? 0) >= PER_HOUR) return json({ error: "rate_limited" }, 429);

  await env.DB.prepare(
    "INSERT INTO tickets (day, hour, ip_hash, category, lang, message, contact) VALUES (?, ?, ?, ?, ?, ?, ?)"
  ).bind(day, hour, ipHash, t.category, t.lang, t.message, t.contact).run();
  return json({ ok: true });
}
