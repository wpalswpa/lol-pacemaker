/**
 * AGENTS §15: Riot account ownership verification (profile-icon challenge until RSO approval)
 * and self-only AI match review ("문철 분석") backed by MATCH-V5 timeline evidence.
 *
 * Security invariants:
 * - Never accepts another player's Riot ID for review; only inspects matches belonging to the
 *   PUUID inside the HMAC-SHA256 signed Bearer token.
 * - No passwords, no Riot credentials, no raw PUUID stored in D1 (only SHA-256 hash).
 * - Challenge expires in 10 minutes; max 5 challenges/hour per account.
 */

import { REVIEW_BASELINE } from "./review_baseline.js";

export const PLATFORM_HOST = {
  kr: "kr.api.riotgames.com",
  jp: "jp1.api.riotgames.com",
  na: "na1.api.riotgames.com",
  br: "br1.api.riotgames.com",
  lan: "la1.api.riotgames.com",
  las: "la2.api.riotgames.com",
  euw: "euw1.api.riotgames.com",
  eune: "eun1.api.riotgames.com",
  sea: "sg2.api.riotgames.com",
};

export const REGION_HOST = {
  kr: "asia.api.riotgames.com",
  jp: "asia.api.riotgames.com",
  na: "americas.api.riotgames.com",
  br: "americas.api.riotgames.com",
  lan: "americas.api.riotgames.com",
  las: "americas.api.riotgames.com",
  euw: "europe.api.riotgames.com",
  eune: "europe.api.riotgames.com",
  sea: "sea.api.riotgames.com",
};

// Free default summoner icons available on every Riot account (0..28)
const DEFAULT_ICONS = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28];
const TOKEN_TTL_SEC = 30 * 24 * 3600;
const CHALLENGE_TTL_SEC = 600;

const FUN_PREFIXES = [
  "머리손질하는", "점멸아끼는", "강타놓친", "부쉬숨은", "집안가는", "궁아끼는",
  "CS놓치는", "바론스틸하는", "우물지키는", "벽못넘는", "와드안박는", "솔킬따인",
  "눈덩이맞은", "귀환끊는", "포탑때리는", "탈진든", "미드오픈한", "칼바람장인",
];
const FUN_CHAMPS = [
  "라이즈", "이즈리얼", "티모", "야스오", "리신", "블리츠", "가렌", "카서스",
  "직스", "베인", "룰루", "징크스", "아무무", "애쉬", "탈론", "제드", "다리우스", "럭스", "아칼리", "사일러스",
];
const FUN_SPECIALS = ["머리손질하는라이즈", "이즈리얼이즈페이크", "점멸없는야스오", "강타아끼는리신", "부쉬속가렌"];

export function randomChampNickname() {
  if (Math.random() < 0.25) return FUN_SPECIALS[Math.floor(Math.random() * FUN_SPECIALS.length)];
  const p = FUN_PREFIXES[Math.floor(Math.random() * FUN_PREFIXES.length)];
  const c = FUN_CHAMPS[Math.floor(Math.random() * FUN_CHAMPS.length)];
  return p + c;
}

function cleanDisplayName(raw) {
  const s = String(raw || "").trim().replace(/\s+/g, " ");
  if (s.length < 2 || s.length > 20) return "";
  return s;
}

function json(body, status, headers) {
  return new Response(JSON.stringify(body), { status, headers });
}

export async function sha256Hex(str) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(str));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function b64uEncode(bytes) {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function b64uDecode(str) {
  const pad = "=".repeat((4 - (str.length % 4)) % 4);
  const bin = atob(str.replace(/-/g, "+").replace(/_/g, "/") + pad);
  return Uint8Array.from(bin, (c) => c.charCodeAt(0));
}

async function hmacKey(env) {
  const secret = env.AUTH_SECRET || env.ADMIN_TOKEN || env.SUPPORT_SALT;
  if (!secret) throw new Error("auth_secret_missing");
  return crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign", "verify"],
  );
}

export async function signToken(payload, env) {
  const key = await hmacKey(env);
  const body = b64uEncode(new TextEncoder().encode(JSON.stringify(payload)));
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(body));
  return `v1.${body}.${b64uEncode(new Uint8Array(sig))}`;
}

export async function verifyStateToken(raw, env) {
  const parts = String(raw || "").trim().split(".");
  if (parts.length !== 3 || parts[0] !== "v1") return null;
  try {
    const key = await hmacKey(env);
    const ok = await crypto.subtle.verify("HMAC", key, b64uDecode(parts[2]), new TextEncoder().encode(parts[1]));
    if (!ok) return null;
    const payload = JSON.parse(new TextDecoder().decode(b64uDecode(parts[1])));
    if (!payload.exp || payload.exp < Math.floor(Date.now() / 1000)) return null;
    return payload;
  } catch {
    return null;
  }
}

export async function verifyToken(request, env) {
  const auth = request.headers.get("Authorization") || "";
  if (!auth.startsWith("Bearer ")) return null;
  const payload = await verifyStateToken(auth.slice(7).trim(), env);
  if (!payload || (!payload.user_id && !payload.puuid)) return null;
  return payload;
}

export async function isAdminSession(session, env) {
  if (!session?.email_hash || !env.ADMIN_EMAILS) return false;
  const allowed = String(env.ADMIN_EMAILS).split(",").map((s) => s.trim().toLowerCase()).filter(Boolean);
  if (!allowed.length) return false;
  const salt = env.SUPPORT_SALT || "";
  for (const em of allowed) {
    const h = await sha256Hex(`admin_email:${em}:${salt}`);
    if (h === session.email_hash) return true;
  }
  return false;
}

export async function handleAuthChallenge(request, env, headers) {
  if (request.method !== "POST") return json({ error: "method_not_allowed" }, 405, headers);
  if (!env.RIOT_API_KEY || !env.DB) return json({ error: "service_unavailable" }, 503, headers);

  let body;
  try { body = await request.json(); } catch { return json({ error: "invalid_json" }, 400, headers); }
  if (body.consent !== true) return json({ error: "consent_required" }, 400, headers);
  const riotId = String(body.riot_id || "").trim();
  const region = String(body.region || "kr").toLowerCase();
  const host = REGION_HOST[region] || REGION_HOST.kr;
  const plat = PLATFORM_HOST[region] || PLATFORM_HOST.kr;
  if (!riotId.includes("#")) return json({ error: "invalid_riot_id_format" }, 400, headers);
  const [gameName, tagLine] = riotId.split("#", 2).map((s) => s.trim());
  if (!gameName || !tagLine) return json({ error: "invalid_riot_id_format" }, 400, headers);

  const riotHeaders = { "X-Riot-Token": env.RIOT_API_KEY };
  const accRes = await fetch(
    `https://${host}/riot/account/v1/accounts/by-riot-id/${encodeURIComponent(gameName)}/${encodeURIComponent(tagLine)}`,
    { headers: riotHeaders },
  );
  if (!accRes.ok) return json({ error: "summoner_not_found" }, accRes.status, headers);
  const acc = await accRes.json();

  const sumRes = await fetch(
    `https://${plat}/lol/summoner/v4/summoners/by-puuid/${encodeURIComponent(acc.puuid)}`,
    { headers: riotHeaders },
  );
  if (!sumRes.ok) return json({ error: "summoner_not_found" }, sumRes.status, headers);
  const sum = await sumRes.json();

  const now = Math.floor(Date.now() / 1000);
  const puuidHash = await sha256Hex(acc.puuid + (env.SUPPORT_SALT || ""));
  const recent = await env.DB.prepare(
    "SELECT COUNT(*) AS cnt FROM auth_challenges WHERE puuid_hash = ? AND created_at > ?",
  ).bind(puuidHash, now - 3600).first();
  if ((recent?.cnt || 0) >= 5) {
    return json({ error: "too_many_attempts" }, 429, headers);
  }

  const candidates = DEFAULT_ICONS.filter((id) => id !== sum.profileIconId);
  const targetIcon = candidates[Math.floor(Math.random() * candidates.length)];
  const challengeId = crypto.randomUUID();
  const canonicalId = `${acc.gameName}#${acc.tagLine}`;
  const displayName = cleanDisplayName(body.display_name) || randomChampNickname();

  await env.DB.prepare(
    "INSERT INTO auth_challenges (challenge_id, puuid, puuid_hash, riot_id, region, display_name, target_icon, created_at, expires_at, used) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
  ).bind(challengeId, acc.puuid, puuidHash, canonicalId, region, displayName, targetIcon, now, now + CHALLENGE_TTL_SEC).run();

  return json({
    challenge_id: challengeId,
    riot_id: canonicalId,
    region: region.toUpperCase(),
    display_name: displayName,
    current_icon: sum.profileIconId,
    target_icon: targetIcon,
    expires_in: CHALLENGE_TTL_SEC,
  }, 200, headers);
}

export async function handleAuthVerify(request, env, headers) {
  if (request.method !== "POST") return json({ error: "method_not_allowed" }, 405, headers);
  if (!env.RIOT_API_KEY || !env.DB) return json({ error: "service_unavailable" }, 503, headers);

  let body;
  try { body = await request.json(); } catch { return json({ error: "invalid_json" }, 400, headers); }
  const challengeId = String(body.challenge_id || "").trim();
  if (!challengeId) return json({ error: "missing_challenge_id" }, 400, headers);

  const row = await env.DB.prepare(
    "SELECT * FROM auth_challenges WHERE challenge_id = ?",
  ).bind(challengeId).first();
  const now = Math.floor(Date.now() / 1000);
  if (!row || row.used || row.expires_at < now) {
    return json({ error: "challenge_expired" }, 400, headers);
  }

  const plat = PLATFORM_HOST[row.region] || PLATFORM_HOST.kr;
  const sumRes = await fetch(
    `https://${plat}/lol/summoner/v4/summoners/by-puuid/${encodeURIComponent(row.puuid)}`,
    { headers: { "X-Riot-Token": env.RIOT_API_KEY }, cf: { cacheTtl: 0 } },
  );
  if (!sumRes.ok) return json({ error: "riot_check_failed" }, 502, headers);
  const sum = await sumRes.json();

  if (Number(sum.profileIconId) !== Number(row.target_icon)) {
    return json({
      error: "icon_mismatch",
      expected: row.target_icon,
      actual: sum.profileIconId,
    }, 400, headers);
  }

  const nowIso = new Date().toISOString();
  const day = nowIso.slice(0, 10);
  const existing = await env.DB.prepare("SELECT display_name FROM linked_accounts WHERE puuid_hash = ?").bind(row.puuid_hash).first();
  const finalDisplayName = cleanDisplayName(row.display_name) || existing?.display_name || randomChampNickname();

  await env.DB.prepare("UPDATE auth_challenges SET used = 1 WHERE challenge_id = ?").bind(challengeId).run();
  await env.DB.prepare(
    "INSERT INTO linked_accounts (puuid_hash, riot_id, region, display_name, consent_version, consented_at, first_linked_day, last_verified_day) " +
    "VALUES (?, ?, ?, ?, '1', ?, ?, ?) " +
    "ON CONFLICT(puuid_hash) DO UPDATE SET riot_id = excluded.riot_id, region = excluded.region, " +
    "display_name = CASE WHEN excluded.display_name != '' THEN excluded.display_name ELSE linked_accounts.display_name END, " +
    "consent_version = excluded.consent_version, consented_at = excluded.consented_at, last_verified_day = excluded.last_verified_day",
  ).bind(row.puuid_hash, row.riot_id, row.region.toUpperCase(), finalDisplayName, nowIso, day, day).run();

  // If caller is already logged in via OAuth (`users`), attach Riot PUUID & profile icon directly to their account
  const oauthSession = await verifyToken(request, env);
  if (oauthSession?.user_id) {
    await env.DB.prepare(
      "UPDATE users SET puuid = ?, puuid_hash = ?, riot_id = ?, region = ?, profile_icon_id = ? WHERE user_id = ?",
    ).bind(row.puuid, row.puuid_hash, row.riot_id, row.region, Number(sum.profileIconId || 0), oauthSession.user_id).run();
    const u = await env.DB.prepare("SELECT * FROM users WHERE user_id = ?").bind(oauthSession.user_id).first();
    return json({
      riot_linked: true,
      riot_id: row.riot_id,
      region: row.region.toUpperCase(),
      profile_icon_id: Number(sum.profileIconId || 0),
      display_name: u?.display_name || finalDisplayName,
      consented_at: (u?.consented_at || day).slice(0, 10),
    }, 200, headers);
  }

  const token = await signToken({
    puuid: row.puuid,
    puuid_hash: row.puuid_hash,
    riot_id: row.riot_id,
    region: row.region,
    profile_icon_id: Number(sum.profileIconId || 0),
    consented_at: day,
    exp: now + TOKEN_TTL_SEC,
  }, env);

  return json({
    token,
    riot_linked: true,
    riot_id: row.riot_id,
    region: row.region.toUpperCase(),
    profile_icon_id: Number(sum.profileIconId || 0),
    display_name: finalDisplayName,
    consented_at: day,
  }, 200, headers);
}

async function syncRiotIdByPuuid(puuid, region, fallbackRiotId, env, cacheTtl = 300) {
  if (!env.RIOT_API_KEY || !puuid) return fallbackRiotId;
  const host = REGION_HOST[String(region || "kr").toLowerCase()] || REGION_HOST.kr;
  try {
    const res = await fetch(
      `https://${host}/riot/account/v1/accounts/by-puuid/${encodeURIComponent(puuid)}`,
      { headers: { "X-Riot-Token": env.RIOT_API_KEY }, cf: { cacheTtl } },
    );
    if (!res.ok) return fallbackRiotId;
    const acc = await res.json();
    if (acc.gameName && acc.tagLine) return `${acc.gameName}#${acc.tagLine}`;
  } catch {}
  return fallbackRiotId;
}

export async function handleAuthMe(request, env, headers) {
  if (request.method !== "GET") return json({ error: "method_not_allowed" }, 405, headers);
  const session = await verifyToken(request, env);
  if (!session) return json({ linked: false, logged_in: false }, 200, headers);

  // Path 1: Real OAuth member (`users` table)
  if (session.user_id && env.DB) {
    const u = await env.DB.prepare("SELECT * FROM users WHERE user_id = ?").bind(session.user_id).first();
    if (!u) return json({ linked: false, logged_in: false }, 200, headers);
    let riotId = u.riot_id || "";
    if (u.puuid) {
      const latest = await syncRiotIdByPuuid(u.puuid, u.region, riotId, env, 300);
      if (latest && latest !== riotId) {
        riotId = latest;
        await env.DB.prepare("UPDATE users SET riot_id = ? WHERE user_id = ?").bind(riotId, u.user_id).run();
      }
    }
    const isAdmin = await isAdminSession(session, env);
    return json({
      logged_in: true,
      linked: Boolean(u.puuid),
      riot_linked: Boolean(u.puuid),
      is_admin: isAdmin,
      provider: u.provider,
      display_name: u.display_name,
      avatar_url: u.avatar_url || "",
      profile_icon_id: u.profile_icon_id || 0,
      riot_id: riotId,
      region: String(u.region || "kr").toUpperCase(),
      consented_at: (u.consented_at || "").slice(0, 10),
    }, 200, headers);
  }

  // Path 2: Legacy direct Riot-verified session
  const puuidHash = session.puuid_hash || await sha256Hex(session.puuid + (env.SUPPORT_SALT || ""));
  const acct = env.DB
    ? await env.DB.prepare("SELECT * FROM linked_accounts WHERE puuid_hash = ?").bind(puuidHash).first()
    : null;
  if (env.DB && !acct) return json({ linked: false, logged_in: false }, 200, headers);

  const latestRiotId = await syncRiotIdByPuuid(session.puuid, session.region, acct?.riot_id || session.riot_id, env, 300);
  if (env.DB && acct && latestRiotId && latestRiotId !== acct.riot_id) {
    await env.DB.prepare("UPDATE linked_accounts SET riot_id = ? WHERE puuid_hash = ?").bind(latestRiotId, puuidHash).run();
  }

  return json({
    logged_in: true,
    linked: true,
    riot_linked: true,
    provider: "riot_icon",
    riot_id: latestRiotId || acct?.riot_id || session.riot_id,
    region: (acct?.region || session.region || "kr").toUpperCase(),
    profile_icon_id: session.profile_icon_id || 0,
    display_name: acct?.display_name || randomChampNickname(),
    consented_at: (acct?.consented_at || session.consented_at || "").slice(0, 10),
  }, 200, headers);
}

export async function handleAccount(request, env, headers) {
  const session = await verifyToken(request, env);
  if (!session) return json({ error: "auth_required" }, 401, headers);
  if (!env.DB) return json({ error: "service_unavailable" }, 503, headers);

  if (session.user_id) {
    const u = await env.DB.prepare("SELECT * FROM users WHERE user_id = ?").bind(session.user_id).first();
    if (!u) return json({ error: "account_not_found" }, 404, headers);

    if (request.method === "PATCH") {
      let body;
      try { body = await request.json(); } catch { return json({ error: "invalid_json" }, 400, headers); }
      let nextDisplay = u.display_name;
      if ("display_name" in body) {
        const cleaned = cleanDisplayName(body.display_name);
        if (body.display_name && !cleaned) return json({ error: "invalid_display_name" }, 400, headers);
        nextDisplay = cleaned || randomChampNickname();
        await env.DB.prepare("UPDATE users SET display_name = ? WHERE user_id = ?").bind(nextDisplay, u.user_id).run();
      }
      let nextIconId = u.profile_icon_id || 0;
      let nextAvatarUrl = u.avatar_url || "";
      if ("profile_icon_id" in body) {
        const iconId = Number(body.profile_icon_id);
        if (!Number.isInteger(iconId) || iconId < 0 || iconId > 999999) {
          return json({ error: "invalid_profile_icon" }, 400, headers);
        }
        nextIconId = iconId;
        if (iconId > 0) nextAvatarUrl = "";
        await env.DB.prepare("UPDATE users SET profile_icon_id = ?, avatar_url = ? WHERE user_id = ?")
          .bind(nextIconId, nextAvatarUrl, u.user_id).run();
      }
      if ("avatar_url" in body) {
        const av = String(body.avatar_url || "");
        const validDataUrl = av === "" || (/^data:image\/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+$/.test(av) && av.length <= 60000);
        if (!validDataUrl) return json({ error: "invalid_avatar" }, 400, headers);
        nextAvatarUrl = av;
        if (av) nextIconId = 0;
        await env.DB.prepare("UPDATE users SET avatar_url = ?, profile_icon_id = ? WHERE user_id = ?")
          .bind(nextAvatarUrl, nextIconId, u.user_id).run();
      }
      let nextRiotId = u.riot_id || "";
      if (body.sync_riot_id && u.puuid) {
        nextRiotId = await syncRiotIdByPuuid(u.puuid, u.region, nextRiotId, env, 0);
        if (nextRiotId && nextRiotId !== u.riot_id) {
          await env.DB.prepare("UPDATE users SET riot_id = ? WHERE user_id = ?").bind(nextRiotId, u.user_id).run();
        }
      }
      return json({
        riot_id: nextRiotId,
        region: String(u.region || "kr").toUpperCase(),
        display_name: nextDisplay,
        avatar_url: nextAvatarUrl,
        profile_icon_id: nextIconId,
        consented_at: (u.consented_at || "").slice(0, 10),
      }, 200, headers);
    }

    if (request.method === "DELETE") {
      await env.DB.prepare("DELETE FROM users WHERE user_id = ?").bind(u.user_id).run();
      if (u.puuid_hash) {
        await env.DB.prepare("DELETE FROM linked_accounts WHERE puuid_hash = ?").bind(u.puuid_hash).run();
        await env.DB.prepare("DELETE FROM auth_challenges WHERE puuid_hash = ?").bind(u.puuid_hash).run();
      }
      return json({ deleted: true }, 200, headers);
    }
    return json({ error: "method_not_allowed" }, 405, headers);
  }

  const puuidHash = session.puuid_hash || await sha256Hex(session.puuid + (env.SUPPORT_SALT || ""));
  if (request.method === "PATCH") {
    let body;
    try { body = await request.json(); } catch { return json({ error: "invalid_json" }, 400, headers); }
    const acct = await env.DB.prepare("SELECT * FROM linked_accounts WHERE puuid_hash = ?").bind(puuidHash).first();
    if (!acct) return json({ error: "account_not_found" }, 404, headers);

    let nextDisplay = acct.display_name || randomChampNickname();
    if ("display_name" in body) {
      const cleaned = cleanDisplayName(body.display_name);
      if (body.display_name && !cleaned) return json({ error: "invalid_display_name" }, 400, headers);
      nextDisplay = cleaned || randomChampNickname();
      await env.DB.prepare("UPDATE linked_accounts SET display_name = ? WHERE puuid_hash = ?").bind(nextDisplay, puuidHash).run();
    }

    let nextRiotId = acct.riot_id;
    if (body.sync_riot_id) {
      nextRiotId = await syncRiotIdByPuuid(session.puuid, session.region, acct.riot_id, env, 0);
      if (nextRiotId && nextRiotId !== acct.riot_id) {
        await env.DB.prepare("UPDATE linked_accounts SET riot_id = ? WHERE puuid_hash = ?").bind(nextRiotId, puuidHash).run();
      }
    }

    return json({
      riot_id: nextRiotId,
      region: acct.region,
      display_name: nextDisplay,
      consented_at: (acct.consented_at || "").slice(0, 10),
    }, 200, headers);
  }

  if (request.method === "DELETE") {
    await env.DB.prepare("DELETE FROM linked_accounts WHERE puuid_hash = ?").bind(puuidHash).run();
    await env.DB.prepare("DELETE FROM auth_challenges WHERE puuid_hash = ?").bind(puuidHash).run();
    return json({ deleted: true }, 200, headers);
  }

  return json({ error: "method_not_allowed" }, 405, headers);
}

// ---------------------------------------------------------------------------------------------
// AGENTS §15·§18·§19: self-review analysis. 100% deterministic statistics, no LLM / Workers AI.
// `analyzeMatch` is pure (no fetch) so scripts/eval_review.mjs can replay real matches offline.
// ---------------------------------------------------------------------------------------------

// Classify Summoner's Rift (0..15000) coordinates into human-readable map zone
const ZONES = {
  skirmish: { en: "Skirmish", ko: "교전 지역", ja: "交戦エリア", es: "Escaramuza", pt: "Escaramuça" },
  baron: { en: "Baron/Herald River", ko: "바론·전령 강가", ja: "バロン・ヘラルド川", es: "Río de Barón/Heraldo", pt: "Rio do Barão/Arauto" },
  dragon: { en: "Dragon River", ko: "드래곤 강가", ja: "ドラゴン川", es: "Río del Dragón", pt: "Rio do Dragão" },
  mid_river: { en: "Mid River", ko: "중앙 강가", ja: "ミッド川", es: "Río central", pt: "Rio central" },
  top: { en: "Top Lane", ko: "탑 라인", ja: "トップレーン", es: "Carril superior", pt: "Rota superior" },
  bot: { en: "Bot Lane", ko: "바텀 라인", ja: "ボットレーン", es: "Carril inferior", pt: "Rota inferior" },
  mid: { en: "Mid Lane", ko: "미드 라인", ja: "ミッドレーン", es: "Carril central", pt: "Rota do meio" },
  ally_jg: { en: "Ally Jungle", ko: "아군 정글", ja: "味方ジャングル", es: "Jungla aliada", pt: "Selva aliada" },
  enemy_jg: { en: "Enemy Jungle", ko: "상대 정글", ja: "敵ジャングル", es: "Jungla enemiga", pt: "Selva inimiga" },
  aram: { en: "ARAM bridge", ko: "칼바람 다리", ja: "ARAMの橋", es: "Puente de ARAM", pt: "Ponte de ARAM" },
};
function normLang(lang) {
  const l = String(lang || "en").toLowerCase().slice(0, 2);
  return ["en", "ko", "ja", "es", "pt"].includes(l) ? l : "en";
}
function pickL(map, lang) { return map[normLang(lang)] || map.en; }

function classifyZone(x, y, teamId, lang) {
  const L = normLang(lang);
  if (typeof x !== "number" || typeof y !== "number") return ZONES.skirmish[L];
  const inRiver = Math.abs((x + y) - 14800) < 1700 && x > 2800 && x < 12000 && y > 2800 && y < 12000;
  if (inRiver) {
    if (x < 6800 && y > 7800) return ZONES.baron[L];
    if (x > 7800 && y < 6800) return ZONES.dragon[L];
    return ZONES.mid_river[L];
  }
  if (x < 2800 || y > 12200) return ZONES.top[L];
  if (y < 2800 || x > 12200) return ZONES.bot[L];
  if (Math.abs(x - y) < 1500) return ZONES.mid[L];
  const isBlueSide = (x + y) < 14800;
  const isAllySide = (teamId === 100 && isBlueSide) || (teamId === 200 && !isBlueSide);
  return isAllySide ? ZONES.ally_jg[L] : ZONES.enemy_jg[L];
}

function fmtTime(ms) {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

// Rules (AGENTS §19). Thresholds live here only.
export const REVIEW_RULES = {
  remakeSec: 300,          // < 5:00 or gameEndedInEarlySurrender -> no analysis
  gd15Ms: 15 * 60000,      // 15-minute frame must exist for "15m gold diff"
  strengthT: 55,           // a strength needs T >= 55 (z >= +0.5)
  weaknessT: 45,           // a weakness needs T < 45 (z < -0.5)
  fightGapMs: 20000,       // kills closer than 20 s belong to one fight
  fightMinKills: 3,
  gankBeforeMs: 14 * 60000,
  objVisionMs: 60000,
  powerPlayMs: 180000,     // baron buff duration
  swingFrames: 3,          // turning point = largest 3-minute team gold swing
  maxItems: 3,
};

const LOWER_IS_BETTER = new Set(["deaths_per10", "gank_deaths", "first_death_fights"]);

export function queueGroup(info) {
  const map = Number(info?.mapId || 0);
  const q = Number(info?.queueId || 0);
  if (map === 12 || q === 450) return "aram";
  if (map === 11 || q === 420 || q === 440 || q === 400 || q === 430 || q === 490 || q === 480) return q === 420 || q === 440 ? "rift" : "other";
  return "unsupported";
}

export function isRemake(info) {
  const parts = info?.participants || [];
  return Number(info?.gameDuration || 0) < REVIEW_RULES.remakeSec || parts.some((p) => p.gameEndedInEarlySurrender);
}

export function durationBucket(durationMin, edges) {
  if (!edges || edges.length !== 2) return "ALL";
  return durationMin < edges[0] ? "short" : durationMin < edges[1] ? "mid" : "long";
}

function normCdf(z) {
  const t = 1 / (1 + 0.2316419 * Math.abs(z));
  const d = 0.3989422804014327 * Math.exp((-z * z) / 2);
  const p = d * t * (0.31938153 + t * (-0.356563782 + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429))));
  return z >= 0 ? 1 - p : p;
}

/** Baseline cell lookup: queue -> position -> duration bucket, falling back to ALL (n >= min_n only). */
export function baselineCell(baseline, group, position, bucket, metric) {
  const g = baseline?.cells?.[group === "other" ? "rift" : group];
  if (!g) return null;
  const minN = baseline.min_n || 30;
  for (const [pos, b] of [[position, bucket], [position, "ALL"], ["ALL", bucket], ["ALL", "ALL"]]) {
    const c = g?.[pos || "ALL"]?.[b]?.[metric];
    if (c && c.n >= minN && c.std > 0) return { ...c, position: pos || "ALL", bucket: b };
  }
  return null;
}

// AGENTS §28: the same AFK rule as ml/pipeline.py `afk_participants` (XP stalls 4 minutes after minute 3).
export const AFK_RULE = { fromFrame: 3, stallFrames: 4, xpEps: 5 };
export function afkParticipants(timeline) {
  const frames = timeline?.info?.frames || [];
  const out = [];
  for (let pid = 1; pid <= 10; pid++) {
    const xs = frames.map((f) => Number(f?.participantFrames?.[String(pid)]?.xp || 0));
    let stall = 0;
    for (let i = AFK_RULE.fromFrame; i + 1 < xs.length; i++) {
      stall = xs[i + 1] - xs[i] <= AFK_RULE.xpEps ? stall + 1 : 0;
      if (stall >= AFK_RULE.stallFrames) { out.push(pid); break; }
    }
  }
  return out;
}
export function seasonOf(info) {
  const n = parseInt(String(info?.gameVersion || "0").split(".")[0], 10);
  return Number.isFinite(n) ? n : 0;
}

function zOf(value, cell, metric) {
  if (value === null || value === undefined || !cell) return null;
  const z = (value - cell.mean) / cell.std;
  return LOWER_IS_BETTER.has(metric) ? -z : z;
}

function profile(key, label, metric, value, unit, cell) {
  const zRaw = zOf(value, cell, metric);
  const z = Math.max(-3, Math.min(3, zRaw ?? 0));
  const t = Number((50 + 10 * z).toFixed(1));
  const pct = Number((normCdf(z) * 100).toFixed(1));
  const gradeCode = t >= 70 ? "elite" : t >= 60 ? "high" : t >= 40 ? "avg" : t >= 30 ? "low" : "risk";
  return {
    key, label, metric, raw: value === null ? null : Number(value.toFixed(metric === "lane_gd15" ? 0 : 2)), unit,
    t_score: t, percentile: pct, grade_code: gradeCode, z: zRaw === null ? null : Number(z.toFixed(3)),
    radar: Math.round(Math.max(10, Math.min(100, pct))),
    baseline: cell ? { mean: cell.mean, std: cell.std, n: cell.n, position: cell.position, bucket: cell.bucket } : null,
  };
}

const GRADE_LABEL = {
  ko: { elite: "최상", high: "높음", avg: "보통", low: "낮음", risk: "위험" },
  en: { elite: "Elite", high: "High", avg: "Avg", low: "Low", risk: "Risk" },
  ja: { elite: "最高", high: "高い", avg: "平均", low: "低い", risk: "注意" },
  es: { elite: "Élite", high: "Alto", avg: "Medio", low: "Bajo", risk: "Riesgo" },
  pt: { elite: "Elite", high: "Alto", avg: "Médio", low: "Baixo", risk: "Risco" },
};

/**
 * Per-player raw metrics + timeline patterns. Shared by the review and by
 * scripts/build_review_baseline.mjs, so the baseline and the review use one definition.
 */
export function playerMetrics(match, timeline, pid) {
  const info = match?.info || {};
  const parts = info.participants || [];
  const me = parts.find((p) => p.participantId === pid);
  if (!me) return null;
  const group = queueGroup(info);
  const myTeam = me.teamId;
  const durMin = Math.max(1, Number(info.gameDuration || 60) / 60);
  const team = parts.filter((p) => p.teamId === myTeam);
  const enemy = parts.filter((p) => p.teamId !== myTeam);
  const sum = (arr, k) => arr.reduce((a, p) => a + (p[k] || 0), 0);
  const teamKills = sum(team, "kills");
  const position = group === "aram" ? "ALL" : (me.teamPosition || "ALL");
  const opp = group !== "aram" && me.teamPosition
    ? enemy.find((p) => p.teamPosition === me.teamPosition) || null
    : null;

  const m = {
    group, position, durMin, win: Boolean(me.win), oppPid: opp?.participantId ?? null,
    cs_per_min: ((me.totalMinionsKilled || 0) + (me.neutralMinionsKilled || 0)) / durMin,
    gold_per_min: (me.goldEarned || 0) / durMin,
    kp: teamKills > 0 ? (100 * ((me.kills || 0) + (me.assists || 0))) / teamKills : null,
    vision_per_min: (me.visionScore || 0) / durMin,
    deaths_per10: (me.deaths || 0) / (durMin / 10),
    dmg_share: sum(team, "totalDamageDealtToChampions") > 0
      ? (100 * (me.totalDamageDealtToChampions || 0)) / sum(team, "totalDamageDealtToChampions") : null,
    taken_share: sum(team, "totalDamageTaken") > 0
      ? (100 * ((me.totalDamageTaken || 0) + (me.damageSelfMitigated || 0)))
        / team.reduce((a, p) => a + (p.totalDamageTaken || 0) + (p.damageSelfMitigated || 0), 0) : null,
    lane_gd15: null,
  };

  const frames = timeline?.info?.frames || [];
  const events = frames.flatMap((f) => f.events || []);
  const pTeam = Object.fromEntries(parts.map((p) => [p.participantId, p.teamId]));
  const pat = {};
  if (frames.length) {
    const f15 = frames.find((f) => (f.timestamp || 0) >= REVIEW_RULES.gd15Ms - 5000);
    if (opp && f15 && (f15.timestamp || 0) <= REVIEW_RULES.gd15Ms + 30000) {
      const a = f15.participantFrames?.[String(pid)]?.totalGold;
      const b = f15.participantFrames?.[String(opp.participantId)]?.totalGold;
      if (typeof a === "number" && typeof b === "number") m.lane_gd15 = a - b;
    }
    if (group !== "aram") {
      // Level 6 race vs lane opponent (seconds; + = I got there first)
      const lvl6 = (id) => events.find((e) => e.type === "LEVEL_UP" && e.participantId === id && e.level === 6)?.timestamp;
      const mine = lvl6(pid);
      const theirs = opp ? lvl6(opp.participantId) : undefined;
      if (typeof mine === "number" && typeof theirs === "number") pat.lvl6_lead_sec = (theirs - mine) / 1000;
      // First back (first purchase after 1:30). Reported only, not judged.
      const back = events.find((e) => e.type === "ITEM_PURCHASED" && e.participantId === pid && e.timestamp > 90000);
      if (back) {
        const prev = [...frames].reverse().find((f) => f.timestamp <= back.timestamp);
        pat.first_back_ms = back.timestamp;
        pat.first_back_gold = prev?.participantFrames?.[String(pid)]?.currentGold ?? null;
      }
      // Gank exposure: my early deaths where the enemy jungler took part (laners only)
      if (me.teamPosition && me.teamPosition !== "JUNGLE") {
        const ej = enemy.find((p) => p.teamPosition === "JUNGLE")?.participantId;
        if (ej) {
          pat.gank_deaths = events.filter((e) => e.type === "CHAMPION_KILL" && e.victimId === pid
            && e.timestamp < REVIEW_RULES.gankBeforeMs
            && (e.killerId === ej || (e.assistingParticipantIds || []).includes(ej))).length;
        }
      }
      // Objective prep vision: share of epic monsters with >= 1 of my wards in the prior 60 s
      const objs = [];
      for (const e of events) {
        if (e.type !== "ELITE_MONSTER_KILL") continue;
        const last = objs[objs.length - 1];
        if (last && last.type === e.monsterType && e.timestamp - last.t < 60000) continue; // grub waves = one objective
        objs.push({ type: e.monsterType, t: e.timestamp });
      }
      if (objs.length) {
        const wards = events.filter((e) => e.type === "WARD_PLACED" && e.creatorId === pid).map((e) => e.timestamp);
        const covered = objs.filter((o) => wards.some((w) => w >= o.t - REVIEW_RULES.objVisionMs && w <= o.t)).length;
        pat.obj_vision_rate = (100 * covered) / objs.length;
        pat.obj_count = objs.length;
      }
      // Power play: after my team's baron / inhibitor, did my team take a structure within 180 s?
      const plays = events.filter((e) => (e.type === "ELITE_MONSTER_KILL" && e.monsterType === "BARON_NASHOR" && e.killerTeamId === myTeam)
        || (e.type === "BUILDING_KILL" && e.buildingType === "INHIBITOR_BUILDING" && e.teamId !== myTeam));
      if (plays.length) {
        const conv = plays.filter((pp) => events.some((e) => e.type === "BUILDING_KILL" && e.teamId !== myTeam
          && e.timestamp > pp.timestamp && e.timestamp <= pp.timestamp + REVIEW_RULES.powerPlayMs)).length;
        pat.power_play_rate = (100 * conv) / plays.length;
        pat.power_play_n = plays.length;
      }
    }
    // Fights: kill clusters (gap <= 20 s, >= 3 kills). First-death in a lost fight.
    const kills = events.filter((e) => e.type === "CHAMPION_KILL").sort((a, b) => a.timestamp - b.timestamp);
    const fights = [];
    for (const k of kills) {
      const f = fights[fights.length - 1];
      if (f && k.timestamp - f[f.length - 1].timestamp <= REVIEW_RULES.fightGapMs) f.push(k); else fights.push([k]);
    }
    let firstDeathLost = 0;
    let fightsIn = 0;
    for (const f of fights.filter((x) => x.length >= REVIEW_RULES.fightMinKills)) {
      const involved = f.some((k) => k.victimId === pid || k.killerId === pid || (k.assistingParticipantIds || []).includes(pid));
      if (!involved) continue;
      fightsIn += 1;
      const allyDeaths = f.filter((k) => pTeam[k.victimId] === myTeam).length;
      if (f[0].victimId === pid && allyDeaths > f.length - allyDeaths) firstDeathLost += 1;
    }
    pat.first_death_fights = firstDeathLost;
    pat.fights = fightsIn;
    // Team gold swing per frame (my team - enemy)
    const gd = frames.map((f) => {
      let v = 0;
      for (const [id, pf] of Object.entries(f.participantFrames || {})) v += (pTeam[Number(id)] === myTeam ? 1 : -1) * (pf.totalGold || 0);
      return { t: f.timestamp || 0, v };
    });
    // Turning point (AGENTS §19-5): the final window (ending on the last frame = the nexus push) is a
    // consequence, not a cause, so it is excluded. Windows where the team gold lead flips sign win first.
    const w = REVIEW_RULES.swingFrames;
    const wins = [];
    for (let i = 0; i + w < gd.length - 1; i++) {
      wins.push({ from: gd[i].t, to: gd[i + w].t, delta: gd[i + w].v - gd[i].v, before: gd[i].v, after: gd[i + w].v });
    }
    const pick = (better, flips) => {
      const flipped = wins.filter(flips);
      const pool = flipped.length ? flipped : wins;
      const best = pool.reduce((acc, x) => (!acc || better(x.delta, acc.delta) ? x : acc), null);
      return best && { ...best, flip: flipped.length > 0 };
    };
    const drop = pick((a, b) => a < b, (x) => x.before >= 0 && x.after < 0 && x.delta < 0);
    const rise = pick((a, b) => a > b, (x) => x.before <= 0 && x.after > 0 && x.delta > 0);
    pat.max_drop_3m = drop ? -drop.delta : null;
    pat.max_rise_3m = rise ? rise.delta : null;
    pat._drop = drop;
    pat._rise = rise;
  }
  return { m, pat, me, opp, events };
}

function objName(o, lang) {
  const L = normLang(lang);
  if (o.type === "BUILDING_KILL") {
    return o.buildingType === "INHIBITOR_BUILDING"
      ? pickL({ en: "Inhibitor", ko: "억제기", ja: "インヒビター", es: "Inhibidor", pt: "Inibidor" }, L)
      : pickL({ en: "Tower", ko: "포탑", ja: "タワー", es: "Torre", pt: "Torre" }, L);
  }
  const names = {
    BARON_NASHOR: { en: "Baron", ko: "바론", ja: "バロン", es: "Barón", pt: "Barão" },
    DRAGON: { en: "Dragon", ko: "드래곤", ja: "ドラゴン", es: "Dragón", pt: "Dragão" },
    RIFTHERALD: { en: "Herald", ko: "전령", ja: "ヘラルド", es: "Heraldo", pt: "Arauto" },
    HORDE: { en: "Voidgrubs", ko: "공허 유충", ja: "ヴォイドグラブ", es: "Larvas del Vacío", pt: "Vastilarvas" },
    ATAKHAN: { en: "Atakhan", ko: "아타칸", ja: "アタカン", es: "Atakhan", pt: "Atakhan" },
  };
  return names[o.monsterType]?.[L] || o.monsterType || pickL({ en: "Objective", ko: "오브젝트", ja: "オブジェクト", es: "Objetivo", pt: "Objetivo" }, L);
}

const LABELS = {
  lane: { en: "Laning", ko: "라인전", ja: "レーン戦", es: "Línea", pt: "Rota" },
  economy: { en: "Economy", ko: "경제", ja: "経済", es: "Economía", pt: "Economia" },
  farm: { en: "Farming", ko: "파밍", ja: "ファーム", es: "Farmeo", pt: "Farm" },
  combat: { en: "Combat", ko: "교전", ja: "交戦", es: "Combate", pt: "Combate" },
  vision: { en: "Vision", ko: "시야", ja: "視界", es: "Visión", pt: "Visão" },
  frontline: { en: "Frontline", ko: "탱킹", ja: "耐久", es: "Vanguardia", pt: "Linha de frente" },
  survival: { en: "Survival", ko: "생존", ja: "生存", es: "Supervivencia", pt: "Sobrevivência" },
  damage: { en: "Damage", ko: "딜 기여", ja: "ダメージ", es: "Daño", pt: "Dano" },
};

const PAT_LABELS = {
  lvl6_lead_sec: { en: "Level 6 timing", ko: "6레벨 타이밍", ja: "Lv6タイミング", es: "Nivel 6", pt: "Nível 6" },
  gank_deaths: { en: "Early gank deaths", ko: "초반 갱킹 주의", ja: "序盤ガンク警戒", es: "Gankeos tempranos", pt: "Ganks iniciais" },
  obj_vision_rate: { en: "Objective vision", ko: "오브젝트 시야", ja: "オブジェクト視界", es: "Visión de objetivos", pt: "Visão de objetivos" },
  power_play_rate: { en: "Baron/inhib conversion", ko: "바론·억제기 활용", ja: "バロン・インヒビ活用", es: "Aprovechar Barón", pt: "Aproveitar Barão" },
  first_death_fights: { en: "First death in fights", ko: "한타 첫 데스", ja: "集団戦の先落ち", es: "Primera muerte en peleas", pt: "Primeira morte em lutas" },
};

/** Pure analysis. `baseline` = REVIEW_BASELINE (worker/review_baseline.js). */
export function analyzeMatch(match, timeline, puuid, lang, baseline) {
  const Lc = normLang(lang);
  const info = match?.info || {};
  const me0 = (info.participants || []).find((p) => p.puuid === puuid);
  if (!me0) return { error: "player_not_in_match" };
  const group = queueGroup(info);
  const base = {
    match_id: match?.metadata?.matchId || "", champion: me0.championName, win: Boolean(me0.win),
    kda: `${me0.kills || 0}/${me0.deaths || 0}/${me0.assists || 0}`, queue_group: group,
  };
  if (isRemake(info) || group === "unsupported") {
    const remake = group !== "unsupported";
    const tStr = fmtTime((info.gameDuration || 0) * 1000);
    return {
      ...base, status: remake ? "remake" : "unsupported", metrics: [], overall_perf: null, costly_deaths: [], patterns: {}, turning_point: null,
      coaching: {
        summary: remake
          ? pickL({
            en: `Remake / early surrender (${tStr}): excluded from analysis.`,
            ko: `다시하기·조기 항복 경기(${tStr})라 분석에서 제외했습니다.`,
            ja: `リメイク・早期降伏の試合（${tStr}）のため分析対象外です。`,
            es: `Remake / rendición temprana (${tStr}): excluida del análisis.`,
            pt: `Remake / rendição antecipada (${tStr}): excluída da análise.`,
          }, Lc)
          : pickL({
            en: "This mode is not supported by the analysis metrics.",
            ko: "이 모드는 AI 분석 지표를 지원하지 않습니다.",
            ja: "このモードはAI分析の指標に対応していません。",
            es: "Este modo no es compatible con las métricas de análisis.",
            pt: "Este modo não é compatível com as métricas de análise.",
          }, Lc),
        strengths: [], improvements: [], evidence: [],
      },
      findings: [], limitation: "",
    };
  }

  const pm = playerMetrics(match, timeline, me0.participantId);
  const { m, pat, me, opp, events } = pm;
  const bucket = durationBucket(m.durMin, baseline?.duration_edges?.[group === "other" ? "rift" : group]);
  const cell = (metric) => baselineCell(baseline, group, m.position, bucket, metric);
  const L = (k) => LABELS[k]?.[Lc] || LABELS[k]?.en || k;

  const laneMetric = group === "aram" || m.lane_gd15 === null ? "gold_per_min" : "lane_gd15";
  const defs = group === "aram"
    ? [["economy", "gold_per_min", "G/m"], ["farm", "cs_per_min", "CS/m"], ["combat", "kp", "%"],
      ["frontline", "taken_share", "%"], ["survival", "deaths_per10", "/10m"], ["damage", "dmg_share", "%"]]
    : [["lane", laneMetric, laneMetric === "lane_gd15" ? "G" : "G/m"], ["farm", "cs_per_min", "CS/m"], ["combat", "kp", "%"],
      ["vision", "vision_per_min", "/m"], ["survival", "deaths_per10", "/10m"], ["damage", "dmg_share", "%"]];
  const metrics = defs.map(([key, metric, unit]) => {
    const p = profile(key, L(key), metric, m[metric], unit, cell(metric));
    p.grade = (GRADE_LABEL[Lc] || GRADE_LABEL.en)[p.grade_code];
    return p;
  });

  // Overall: mean z of the six metrics, re-standardized against the same mean z of baseline players
  // (scripts/build_review_baseline.mjs stores it as `composite_z`), so its percentile is not compressed.
  const metricZ = metrics.filter((p) => p.z !== null).map((p) => p.z);
  const meanZ = metricZ.length ? metricZ.reduce((a, b) => a + b, 0) / metricZ.length : null;
  const ovp = profile("overall", pickL({ en: "Overall", ko: "종합", ja: "総合", es: "General", pt: "Geral" }, Lc), "composite_z", meanZ, "z", cell("composite_z"));
  const overall = ovp.baseline && meanZ !== null
    ? { t_score: ovp.t_score, percentile: ovp.percentile, top_pct: Number((100 - ovp.percentile).toFixed(1)),
      grade: (GRADE_LABEL[Lc] || GRADE_LABEL.en)[ovp.grade_code], grade_code: ovp.grade_code, mean_z: Number(meanZ.toFixed(3)), metrics_used: metricZ.length, baseline: ovp.baseline }
    : null;

  // Costly deaths (existing §15 evidence)
  const myTeam = me.teamId;
  const myPid = me.participantId;
  const myWards = events.filter((e) => e.type === "WARD_PLACED" && e.creatorId === myPid);
  const enemyObjectives = events.filter((e) => (e.type === "ELITE_MONSTER_KILL" && e.killerTeamId && e.killerTeamId !== myTeam)
    || (e.type === "BUILDING_KILL" && e.teamId === myTeam));
  const costly = [];
  for (const d of events.filter((e) => e.type === "CHAMPION_KILL" && e.victimId === myPid)) {
    const ts = d.timestamp || 0;
    const lost = [...new Set(enemyObjectives.filter((o) => o.timestamp >= ts && o.timestamp <= ts + 60000).map((o) => objName(o, Lc)))];
    const wardsPrior3m = myWards.filter((w) => w.timestamp >= ts - 180000 && w.timestamp <= ts).length;
    const attackers = 1 + (d.assistingParticipantIds?.length || 0);
    costly.push({
      time: fmtTime(ts), zone: group === "aram" ? ZONES.aram[Lc] : classifyZone(d.position?.x, d.position?.y, myTeam, Lc),
      attackers, objectives_lost_60s: lost, wards_prior_3m: wardsPrior3m, bounty: d.shutdownBounty || 0,
      _score: lost.length * 100 + (ts > 900000 ? 30 : 10) + (group !== "aram" && wardsPrior3m === 0 ? 15 : 0) + attackers * 5 + (d.shutdownBounty || 0) / 50,
    });
  }
  costly.sort((a, b) => b._score - a._score);
  const topDeaths = costly.slice(0, 3).map(({ _score, ...rest }) => rest);

  // Pattern z-scores against the same baseline
  const patternZ = {};
  for (const k of ["lvl6_lead_sec", "gank_deaths", "obj_vision_rate", "power_play_rate", "first_death_fights", "max_drop_3m", "max_rise_3m"]) {
    if (typeof pat[k] !== "number") continue;
    const z = zOf(pat[k], cell(k), k);
    if (z !== null) patternZ[k] = Number(z.toFixed(2));
  }

  // Turning point: the biggest 3-minute team gold swing against (or for) my team, if at least typical size
  let turning = null;
  const swing = m.win ? pat._rise : pat._drop;
  const swingZ = m.win ? patternZ.max_rise_3m : patternZ.max_drop_3m;
  // A window where the lead actually flipped is the turning point whatever its size; otherwise it must be
  // at least as large as the typical biggest swing (z >= 0) to be called one.
  if (swing && group !== "aram" && (swing.flip || (swingZ !== undefined && swingZ >= 0))) {
    const inWin = (e) => e.timestamp >= swing.from && e.timestamp <= swing.to;
    const myDeaths = events.filter((e) => e.type === "CHAMPION_KILL" && e.victimId === myPid && inWin(e));
    const objs = events.filter((e) => inWin(e) && ((e.type === "ELITE_MONSTER_KILL" && e.killerTeamId)
      || e.type === "BUILDING_KILL"));
    const tally = (names) => Object.entries(names.reduce((a, n) => ({ ...a, [n]: (a[n] || 0) + 1 }), {})).map(([n, c]) => (c > 1 ? `${n} ${c}` : n));
    const ours = tally(objs.filter((e) => (e.type === "ELITE_MONSTER_KILL" ? e.killerTeamId === myTeam : e.teamId !== myTeam)).map((e) => objName(e, Lc)));
    const theirs = tally(objs.filter((e) => (e.type === "ELITE_MONSTER_KILL" ? e.killerTeamId !== myTeam : e.teamId === myTeam)).map((e) => objName(e, Lc)));
    turning = {
      from: fmtTime(swing.from), to: fmtTime(swing.to), team_gold_swing: swing.delta, team_gold_before: swing.before,
      my_deaths: myDeaths.map((e) => fmtTime(e.timestamp)), objectives_taken: ours, objectives_lost: theirs, favorable: swing.delta > 0,
    };
  }

  // Coaching: every line comes from one evidence item whose sign matches the side it is on.
  const ev = [];
  for (const p of metrics) {
    if (p.baseline === null || p.raw === null) continue;
    const sign = p.unit === "G" && p.raw > 0 ? "+" : "";
    const ref = `${p.baseline.mean.toFixed(p.metric === "lane_gd15" ? 0 : 2)}`;
    const valTxt = `${sign}${p.raw}${p.unit}`;
    const topP = Math.max(1, Math.round(100 - p.percentile));
    const botP = Math.max(1, Math.round(p.percentile));
    if (p.t_score >= REVIEW_RULES.strengthT) {
      ev.push({ side: "strength", key: p.key, z: (p.t_score - 50) / 10, value: valTxt, text: pickL({
        en: `${p.label} ${valTxt} beat the same-role average (${ref}${p.unit}, top ${topP}%).`,
        ko: `${p.label} ${valTxt} — 같은 포지션 평균(${ref}${p.unit})보다 좋아요 (상위 ${topP}%).`,
        ja: `${p.label} ${valTxt} — 同じロールの平均（${ref}${p.unit}）を上回りました（上位${topP}%）。`,
        es: `${p.label} ${valTxt} superó el promedio del rol (${ref}${p.unit}, top ${topP}%).`,
        pt: `${p.label} ${valTxt} superou a média da função (${ref}${p.unit}, top ${topP}%).`,
      }, Lc) });
    } else if (p.t_score < REVIEW_RULES.weaknessT) {
      ev.push({ side: "improvement", key: p.key, z: (p.t_score - 50) / 10, value: valTxt, text: pickL({
        en: `${p.label} ${valTxt} fell short of the same-role average (${ref}${p.unit}, bottom ${botP}%).`,
        ko: `${p.label} ${valTxt} — 같은 포지션 평균(${ref}${p.unit})보다 아쉬워요 (하위 ${botP}%).`,
        ja: `${p.label} ${valTxt} — 同じロールの平均（${ref}${p.unit}）を下回りました（下位${botP}%）。`,
        es: `${p.label} ${valTxt} quedó por debajo del promedio del rol (${ref}${p.unit}, inferior ${botP}%).`,
        pt: `${p.label} ${valTxt} ficou abaixo da média da função (${ref}${p.unit}, inferior ${botP}%).`,
      }, Lc) });
    }
  }
  const zs = REVIEW_RULES.strengthT / 10 - 5; // +0.5
  const zw = REVIEW_RULES.weaknessT / 10 - 5; // -0.5
  const patText = {
    lvl6_lead_sec: (v, good) => {
      const s = Math.abs(v).toFixed(0);
      return pickL({
        en: `Hit level 6 ${s}s ${good ? "before" : "after"} your lane opponent.`,
        ko: `레벨 6을 맞라인 상대보다 ${s}초 ${good ? "먼저" : "늦게"} 찍었습니다.`,
        ja: `対面より${s}秒${good ? "早く" : "遅く"}レベル6に到達しました。`,
        es: `Llegaste a nivel 6 ${s}s ${good ? "antes" : "después"} que tu rival de línea.`,
        pt: `Pegou nível 6 ${s}s ${good ? "antes" : "depois"} do oponente de rota.`,
      }, Lc);
    },
    gank_deaths: (v) => pickL({
      en: `${v} death(s) before 14:00 involving the enemy jungler.`,
      ko: `14분 전 상대 정글이 관여한 데스 ${v}회 — 정글 위치를 모를 때 라인 앞쪽에 섰던 시간이 길었습니다.`,
      ja: `14分前に敵ジャングラー関与のデスが${v}回 — 位置不明時の押しすぎに注意。`,
      es: `${v} muerte(s) antes del 14:00 con participación del jungla enemigo.`,
      pt: `${v} morte(s) antes dos 14:00 com participação do caçador inimigo.`,
    }, Lc),
    obj_vision_rate: (v, good) => pickL({
      en: `Placed a ward in the 60s before ${v.toFixed(0)}% of epic objectives.`,
      ko: `오브젝트 처치 60초 전 내 와드 설치율 ${v.toFixed(0)}%${good ? " — 오브젝트 시야 준비가 좋았습니다." : " — 오브젝트 전 시야 준비가 부족했습니다."}`,
      ja: `オブジェクト60秒前のワード設置率 ${v.toFixed(0)}%${good ? " — 視界準備が良好でした。" : " — 事前の視界確保が不足していました。"}`,
      es: `Colocaste centinela 60s antes en el ${v.toFixed(0)}% de los objetivos épicos.`,
      pt: `Colocou sentinela 60s antes em ${v.toFixed(0)}% dos objetivos épicos.`,
    }, Lc),
    power_play_rate: (v, good) => pickL({
      en: `${v.toFixed(0)}% of baron/inhibitor leads became structures within 3 min.`,
      ko: `바론·억제기 뒤 3분 안 포탑 전환율 ${v.toFixed(0)}%${good ? "." : " — 이득을 구조물로 바꾸지 못했습니다."}`,
      ja: `バロン・インヒビ後の3分以内タワー破壊率 ${v.toFixed(0)}%${good ? "。" : " — 有利をオブジェクトに繋げられませんでした。"}`,
      es: `${v.toFixed(0)}% de las ventajas de Barón/inhibidor se tradujeron en estructuras en 3 min.`,
      pt: `${v.toFixed(0)}% das vantagens de Barão/inibidor viraram estruturas em 3 min.`,
    }, Lc),
    first_death_fights: (v) => pickL({
      en: `First to die in ${v} lost teamfight(s).`,
      ko: `진 한타 ${v}회에서 첫 데스였습니다 — 아군보다 먼저 들어가거나 먼저 잡혔습니다.`,
      ja: `負けた集団戦${v}回で最初にデスしました — 立ち位置が前寄りでした。`,
      es: `Fuiste el primero en morir en ${v} pelea(s) perdida(s).`,
      pt: `Foi o primeiro a morrer em ${v} luta(s) perdida(s).`,
    }, Lc),
  };
  for (const [k, z] of Object.entries(patternZ)) {
    if (!patText[k]) continue;
    if (z >= zs && !(k === "gank_deaths" || k === "first_death_fights")) ev.push({ side: "strength", key: k, z, value: String(pat[k]), text: patText[k](pat[k], true) });
    else if (z <= zw && (k !== "gank_deaths" && k !== "first_death_fights" || pat[k] > 0)) ev.push({ side: "improvement", key: k, z, value: String(pat[k]), text: patText[k](pat[k], false) });
  }
  if (turning) {
    const amt = Math.abs(turning.team_gold_swing).toLocaleString("en-US");
    const deaths = turning.my_deaths.length ? pickL({
      en: ` Your deaths: ${turning.my_deaths.join(", ")}.`,
      ko: ` 이 구간 내 데스 ${turning.my_deaths.join("·")}.`,
      ja: ` この区間のデス: ${turning.my_deaths.join("・")}。`,
      es: ` Tus muertes: ${turning.my_deaths.join(", ")}.`,
      pt: ` Suas mortes: ${turning.my_deaths.join(", ")}.`,
    }, Lc) : "";
    const lostTxt = turning.objectives_lost.length ? pickL({
      en: ` Lost: ${turning.objectives_lost.join(", ")}.`,
      ko: ` 잃은 것: ${turning.objectives_lost.join("·")}.`,
      ja: ` 失ったもの: ${turning.objectives_lost.join("・")}。`,
      es: ` Perdido: ${turning.objectives_lost.join(", ")}.`,
      pt: ` Perdido: ${turning.objectives_lost.join(", ")}.`,
    }, Lc) : "";
    const tookTxt = turning.objectives_taken.length ? pickL({
      en: ` Took: ${turning.objectives_taken.join(", ")}.`,
      ko: ` 얻은 것: ${turning.objectives_taken.join("·")}.`,
      ja: ` 獲得: ${turning.objectives_taken.join("・")}。`,
      es: ` Obtenido: ${turning.objectives_taken.join(", ")}.`,
      pt: ` Obtido: ${turning.objectives_taken.join(", ")}.`,
    }, Lc) : "";
    if (turning.favorable) {
      ev.push({ side: "strength", key: "turning_point", z: swingZ === undefined ? null : swingZ, basis: swing.flip ? "lead_flip" : "swing_size", value: String(turning.team_gold_swing), text: pickL({
        en: `Key swing ${turning.from}-${turning.to}: team gold grew by +${amt}G in 3 min.${tookTxt}`,
        ko: `${turning.from}~${turning.to} 구간에 팀 골드를 +${amt}G 벌리며 승기를 잡았어요.${tookTxt}`,
        ja: `${turning.from}〜${turning.to}の3分間でチームゴールドを+${amt}G広げ、主導権を握りました。${tookTxt}`,
        es: `Tramo clave ${turning.from}-${turning.to}: el oro del equipo subió +${amt}G en 3 min.${tookTxt}`,
        pt: `Trecho decisivo ${turning.from}-${turning.to}: o ouro da equipe subiu +${amt}G em 3 min.${tookTxt}`,
      }, Lc) });
    } else {
      ev.push({ side: "improvement", key: "turning_point", z: swingZ === undefined ? null : -swingZ, basis: swing.flip ? "lead_flip" : "swing_size", value: String(turning.team_gold_swing), text: pickL({
        en: `Key swing ${turning.from}-${turning.to}: team gold dropped by -${amt}G in 3 min.${deaths}${lostTxt}`,
        ko: `${turning.from}~${turning.to} 구간에 팀 골드가 -${amt}G 밀리며 승기가 기울었어요.${deaths}${lostTxt}`,
        ja: `${turning.from}〜${turning.to}の3分間でチームゴールドが-${amt}G離され、試合が傾きました。${deaths}${lostTxt}`,
        es: `Tramo clave ${turning.from}-${turning.to}: el oro del equipo cayó -${amt}G en 3 min.${deaths}${lostTxt}`,
        pt: `Trecho decisivo ${turning.from}-${turning.to}: o ouro da equipe caiu -${amt}G em 3 min.${deaths}${lostTxt}`,
      }, Lc) });
    }
  }
  const strengthsEv = ev.filter((e) => e.side === "strength").sort((a, b) => (b.z ?? 0) - (a.z ?? 0)).slice(0, REVIEW_RULES.maxItems);
  // The turning point always leads the improvements (AGENTS §19-6); the rest by z, worst first.
  const improvEv = ev.filter((e) => e.side === "improvement")
    .sort((a, b) => (b.key === "turning_point") - (a.key === "turning_point") || (a.z ?? 0) - (b.z ?? 0)).slice(0, REVIEW_RULES.maxItems);
  const strengths = strengthsEv.length ? strengthsEv.map((e) => e.text)
    : [pickL({
      en: "No metric clearly above the role average.",
      ko: "같은 포지션 평균보다 크게 앞선 항목은 없었어요.",
      ja: "同じロールの平均を大きく上回る項目はありませんでした。",
      es: "Ninguna métrica estuvo claramente por encima del promedio.",
      pt: "Nenhuma métrica ficou claramente acima da média.",
    }, Lc)];
  const improvements = improvEv.length ? improvEv.map((e) => e.text)
    : [pickL({
      en: "No metric clearly below the role average.",
      ko: "같은 포지션 평균보다 크게 뒤처진 항목은 없었어요.",
      ja: "同じロールの平均を大きく下回る項目はありませんでした。",
      es: "Ninguna métrica estuvo claramente por debajo del promedio.",
      pt: "Nenhuma métrica ficou claramente abaixo da média.",
    }, Lc)];

  const labelOf = (k) => (LABELS[k] || PAT_LABELS[k])?.[Lc] || (LABELS[k] || PAT_LABELS[k])?.en || pickL({ en: "play", ko: "플레이", ja: "プレイ", es: "juego", pt: "jogo" }, Lc);

  const oppName = opp?.championName || pickL({ en: "no lane opponent", ko: "맞라인 상대 없음", ja: "対面なし", es: "sin rival", pt: "sem oponente" }, Lc);
  const durTxt = fmtTime((info.gameDuration || 0) * 1000);
  const winTxt = me.win
    ? pickL({ en: "Win", ko: "승리", ja: "勝利", es: "Victoria", pt: "Vitória" }, Lc)
    : pickL({ en: "Loss", ko: "패배", ja: "敗北", es: "Derrota", pt: "Derrota" }, Lc);
  const top = strengthsEv[0];
  const low = improvEv[0];
  const head = `${me.championName}${group === "aram" ? "" : ` vs ${oppName}`} (${winTxt}, ${durTxt})`;
  let tail = "";
  if (Lc === "ko") {
    const goodTxt = top ? (top.key === "turning_point" ? `${turning.from}~${turning.to} 구간 주도권` : labelOf(top.key)) : "";
    const fixTxt = low ? (low.key === "turning_point" ? `${turning.from}~${turning.to} 구간에 격차가 크게 벌어졌어요` : `${labelOf(low.key)} 쪽을 먼저 보완해 보세요`) : "";
    if (goodTxt && fixTxt) tail = ` — ${goodTxt}은(는) 좋았지만, ${fixTxt}.`;
    else if (goodTxt) tail = ` — 전반적으로 무난했고, 특히 ${goodTxt}이(가) 좋았어요.`;
    else if (fixTxt) tail = ` — ${fixTxt}.`;
    else tail = " — 전체적으로 평균 수준의 무난한 경기였어요.";
  } else if (Lc === "ja") {
    const goodTxt = top ? (top.key === "turning_point" ? `${turning.from}〜${turning.to}の主導権` : labelOf(top.key)) : "";
    const fixTxt = low ? (low.key === "turning_point" ? `${turning.from}〜${turning.to}で差が広がりました` : `次は${labelOf(low.key)}を意識してみましょう`) : "";
    if (goodTxt && fixTxt) tail = ` — ${goodTxt}は良かったですが、${fixTxt}。`;
    else if (goodTxt) tail = ` — 全体的に安定しており、特に${goodTxt}が優秀でした。`;
    else if (fixTxt) tail = ` — ${fixTxt}。`;
    else tail = " — 全体的に平均的な安定した試合でした。";
  } else if (Lc === "es") {
    const goodTxt = top ? (top.key === "turning_point" ? `el impulso de ${turning.from}-${turning.to}` : labelOf(top.key)) : "";
    const fixTxt = low ? (low.key === "turning_point" ? `la partida se escapó entre ${turning.from} y ${turning.to}` : `mejora ${labelOf(low.key)} en la próxima`) : "";
    if (goodTxt && fixTxt) tail = ` — buen nivel en ${goodTxt}, pero ${fixTxt}.`;
    else if (goodTxt) tail = ` — partida sólida, destacando en ${goodTxt}.`;
    else if (fixTxt) tail = ` — ${fixTxt}.`;
    else tail = " — rendimiento estable dentro del promedio.";
  } else if (Lc === "pt") {
    const goodTxt = top ? (top.key === "turning_point" ? `o ritmo em ${turning.from}-${turning.to}` : labelOf(top.key)) : "";
    const fixTxt = low ? (low.key === "turning_point" ? `o jogo escapou entre ${turning.from} e ${turning.to}` : `foque em ${labelOf(low.key)} na próxima`) : "";
    if (goodTxt && fixTxt) tail = ` — bom destaque em ${goodTxt}, mas ${fixTxt}.`;
    else if (goodTxt) tail = ` — partida consistente, com destaque em ${goodTxt}.`;
    else if (fixTxt) tail = ` — ${fixTxt}.`;
    else tail = " — desempenho estável dentro da média.";
  } else {
    const goodTxt = top ? (top.key === "turning_point" ? `${turning.from}-${turning.to} momentum` : labelOf(top.key)) : "";
    const fixTxt = low ? (low.key === "turning_point" ? `the game slipped during ${turning.from}-${turning.to}` : `focus on ${labelOf(low.key)} next game`) : "";
    if (goodTxt && fixTxt) tail = ` — strong ${goodTxt}, but ${fixTxt}.`;
    else if (goodTxt) tail = ` — solid game overall, especially ${goodTxt}.`;
    else if (fixTxt) tail = ` — ${fixTxt}.`;
    else tail = " — steady average performance overall.";
  }
  const summary = head + tail;

  const findings = [];
  const csm = m.cs_per_min.toFixed(1);
  const kpTxt = m.kp === null ? "-" : `${Math.round(m.kp)}%`;
  const gdStr = m.lane_gd15 !== null ? `${m.lane_gd15 >= 0 ? "+" : ""}${m.lane_gd15}G` : "";
  findings.push(m.lane_gd15 !== null
    ? pickL({
      en: `15m lane gold diff: ${gdStr} (${csm} CS/m, ${kpTxt} KP, ${me.visionScore || 0} vision).`,
      ko: `15분 맞라인 골드 차이: ${gdStr} (분당 CS ${csm}, 킬관여 ${kpTxt}, 시야 점수 ${me.visionScore || 0}).`,
      ja: `15分対面ゴールド差: ${gdStr}（CS/分 ${csm}、キル関与 ${kpTxt}、視界スコア ${me.visionScore || 0}）。`,
      es: `Diferencia de oro al 15m: ${gdStr} (${csm} CS/m, ${kpTxt} KP, ${me.visionScore || 0} visión).`,
      pt: `Diferença de ouro aos 15m: ${gdStr} (${csm} CS/m, ${kpTxt} KP, ${me.visionScore || 0} visão).`,
    }, Lc)
    : pickL({
      en: `Match summary: ${csm} CS/m, ${kpTxt} KP, ${me.visionScore || 0} vision.`,
      ko: `경기 요약: 분당 CS ${csm}, 킬관여 ${kpTxt}, 시야 점수 ${me.visionScore || 0}.`,
      ja: `試合サマリー: CS/分 ${csm}、キル関与 ${kpTxt}、視界スコア ${me.visionScore || 0}。`,
      es: `Resumen: ${csm} CS/m, ${kpTxt} KP, ${me.visionScore || 0} visión.`,
      pt: `Resumo: ${csm} CS/m, ${kpTxt} KP, ${me.visionScore || 0} visão.`,
    }, Lc));
  for (const d of topDeaths) {
    const objTxt = d.objectives_lost_60s.length ? pickL({
      en: ` → lost ${d.objectives_lost_60s.join(", ")} within 60s`,
      ko: ` → 사망 후 60초 내 ${d.objectives_lost_60s.join("·")} 손실`,
      ja: ` → デス後60秒以内に${d.objectives_lost_60s.join("・")}を喪失`,
      es: ` → se perdió ${d.objectives_lost_60s.join(", ")} en 60s`,
      pt: ` → perdeu ${d.objectives_lost_60s.join(", ")} em 60s`,
    }, Lc) : "";
    const wardTxt = group === "aram" ? "" : pickL({
      en: ` (${d.wards_prior_3m} wards in prior 3m)`,
      ko: ` (직전 3분 와드 ${d.wards_prior_3m}개)`,
      ja: `（直前3分のワード${d.wards_prior_3m}個）`,
      es: ` (${d.wards_prior_3m} centinelas en los 3m previos)`,
      pt: ` (${d.wards_prior_3m} sentinelas nos 3m anteriores)`,
    }, Lc);
    findings.push(pickL({
      en: `${d.time} died in ${d.zone} vs ${d.attackers}${objTxt}${wardTxt}.`,
      ko: `${d.time} ${d.zone}에서 ${d.attackers}명에게 사망${objTxt}${wardTxt}.`,
      ja: `${d.time} ${d.zone}で${d.attackers}人にデス${objTxt}${wardTxt}。`,
      es: `${d.time} muerte en ${d.zone} contra ${d.attackers}${objTxt}${wardTxt}.`,
      pt: `${d.time} morte em ${d.zone} contra ${d.attackers}${objTxt}${wardTxt}.`,
    }, Lc));
  }
  if (!topDeaths.length) {
    findings.push(pickL({
      en: "No deaths recorded or timeline unavailable.",
      ko: "기록된 사망이 없거나 타임라인 데이터가 없는 경기입니다.",
      ja: "デス記録がないか、タイムラインデータがありません。",
      es: "Sin muertes registradas o sin datos de línea de tiempo.",
      pt: "Sem mortes registradas ou sem dados de linha do tempo.",
    }, Lc));
  }

  const { _drop, _rise, ...patterns } = pat;
  const sample = metrics.find((p) => p.baseline)?.baseline;
  const afk = afkParticipants(timeline);
  const modeTxt = group === "aram"
    ? pickL({ en: "ARAM", ko: "칼바람", ja: "ARAM", es: "ARAM", pt: "ARAM" }, Lc)
    : pickL({ en: "ranked Summoner's Rift", ko: "솔로 랭크", ja: "ランク戦", es: "clasificatorias", pt: "ranqueadas" }, Lc);
  return {
    ...base, status: "ok",
    data_quality: { afk: afk.length, me_afk: afk.includes(me0.participantId), ally_afk: afk.filter((pid) => (pid <= 5) === (me0.participantId <= 5) && pid !== me0.participantId).length },
    cs_per_min: Number(m.cs_per_min.toFixed(1)), vision_score: me.visionScore || 0,
    kill_participation: m.kp === null ? 0 : Math.round(m.kp), gold_diff_15m: m.lane_gd15,
    costly_deaths: topDeaths, metrics, overall_perf: overall, patterns, pattern_z: patternZ, turning_point: turning,
    baseline: { queue: group === "other" ? "rift" : group, position: m.position, duration_bucket: bucket, source: baseline?.source || null },
    coaching: { summary, strengths, improvements, evidence: [...strengthsEv, ...improvEv].map(({ text, ...rest }) => rest) },
    findings,
    limitation: pickL({
      en: `Basis: compared against real ${modeTxt} matches of the same role and game length${sample ? ` (n=${sample.n})` : ""}. Skillshots, enemy wards, and comms are not in match data.`,
      ko: `비교 기준: Riot 공식 경기 기록을 같은 ${modeTxt} 포지션·경기 길이 평균${sample ? `(표본 ${sample.n}명)` : ""}과 비교했습니다. 스킬 적중·상대 와드·팀 콜은 기록에 없어 제외됩니다.`,
      ja: `比較基準: Riot公式の${modeTxt}試合記録を同じロール・試合時間の平均${sample ? `（標本${sample.n}件）` : ""}と比較しました。スキル命中・敵ワード・コールは含まれません。`,
      es: `Base: comparado con partidas reales de ${modeTxt} del mismo rol y duración${sample ? ` (n=${sample.n})` : ""}. Habilidades, centinelas enemigos y comunicación no están en los datos.`,
      pt: `Base: comparado com partidas reais de ${modeTxt} da mesma função e duração${sample ? ` (n=${sample.n})` : ""}. Habilidades, sentinelas inimigas e comunicação não entram nos dados.`,
    }, Lc),
  };
}

export async function handleReview(request, env, headers) {
  if (request.method !== "POST") return json({ error: "method_not_allowed" }, 405, headers);
  if (!env.RIOT_API_KEY) return json({ error: "service_unavailable" }, 503, headers);

  let body;
  try { body = await request.json(); } catch { return json({ error: "invalid_json" }, 400, headers); }
  const matchId = String(body.match_id || "").trim();
  const lang = String(body.lang || "ko").toLowerCase();
  if (!matchId || matchId.length > 40) return json({ error: "invalid_match_id" }, 400, headers);

  let myPuuid = "";
  let myRegion = String(body.region || "kr").toLowerCase();
  let myRiotId = String(body.riot_id || "").trim();

  // Mode 1: Direct lookup for the searched summoner in Match History (no login required)
  const riotHeaders = { "X-Riot-Token": env.RIOT_API_KEY };
  const host = REGION_HOST[myRegion] || REGION_HOST.kr;
  if (myRiotId.includes("#")) {
    const [gn, tl] = myRiotId.split("#", 2).map((s) => s.trim());
    if (gn && tl) {
      const accRes = await fetch(
        `https://${host}/riot/account/v1/accounts/by-riot-id/${encodeURIComponent(gn)}/${encodeURIComponent(tl)}`,
        { headers: riotHeaders, cf: { cacheTtl: 60 } },
      );
      if (accRes.ok) {
        const acc = await accRes.json();
        myPuuid = acc.puuid || "";
        myRiotId = `${acc.gameName}#${acc.tagLine}`;
      }
    }
  } else {
    // Mode 2: Fallback to logged-in member's linked Riot account
    const session = await verifyToken(request, env);
    if (session?.puuid) {
      myPuuid = session.puuid;
      myRegion = session.region || myRegion;
      myRiotId = session.riot_id || "";
    } else if (session?.user_id && env.DB) {
      const u = await env.DB.prepare("SELECT puuid, region, riot_id FROM users WHERE user_id = ?").bind(session.user_id).first();
      if (u?.puuid) { myPuuid = u.puuid; myRegion = u.region || myRegion; myRiotId = u.riot_id || ""; }
    }
  }

  if (!myPuuid) return json({ error: "summoner_not_found" }, 400, headers);

  const matchHost = REGION_HOST[String(myRegion).toLowerCase()] || REGION_HOST.kr;
  const [mRes, tlRes] = await Promise.all([
    fetch(`https://${matchHost}/lol/match/v5/matches/${encodeURIComponent(matchId)}`, { headers: riotHeaders, cf: { cacheTtl: 300 } }),
    fetch(`https://${matchHost}/lol/match/v5/matches/${encodeURIComponent(matchId)}/timeline`, { headers: riotHeaders, cf: { cacheTtl: 300 } }),
  ]);
  if (!mRes.ok) return json({ error: "match_not_found" }, mRes.status, headers);
  const match = await mRes.json();
  const timeline = tlRes.ok ? await tlRes.json() : null;
  const out = analyzeMatch(match, timeline, myPuuid, lang, REVIEW_BASELINE);
  if (out.error) return json({ error: out.error }, 404, headers);
  return json({ ...out, match_id: matchId, riot_id: myRiotId }, 200, headers);
}
