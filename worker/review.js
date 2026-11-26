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
    return json({
      logged_in: true,
      linked: Boolean(u.puuid),
      riot_linked: Boolean(u.puuid),
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
function classifyZone(x, y, teamId, lang) {
  const ko = lang === "ko";
  if (typeof x !== "number" || typeof y !== "number") return ko ? "교전 지역" : "Skirmish";
  const inRiver = Math.abs((x + y) - 14800) < 1700 && x > 2800 && x < 12000 && y > 2800 && y < 12000;
  if (inRiver) {
    if (x < 6800 && y > 7800) return ko ? "바론·전령 강가" : "Baron/Herald River";
    if (x > 7800 && y < 6800) return ko ? "드래곤 강가" : "Dragon River";
    return ko ? "중앙 강가" : "Mid River";
  }
  if (x < 2800 || y > 12200) return ko ? "탑 라인" : "Top Lane";
  if (y < 2800 || x > 12200) return ko ? "바텀 라인" : "Bot Lane";
  if (Math.abs(x - y) < 1500) return ko ? "미드 라인" : "Mid Lane";
  const isBlueSide = (x + y) < 14800;
  const isAllySide = (teamId === 100 && isBlueSide) || (teamId === 200 && !isBlueSide);
  return isAllySide ? (ko ? "아군 정글" : "Ally Jungle") : (ko ? "상대 정글" : "Enemy Jungle");
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
  const grade = t >= 70 ? "elite" : t >= 60 ? "high" : t >= 40 ? "avg" : t >= 30 ? "low" : "risk";
  return {
    key, label, metric, raw: value === null ? null : Number(value.toFixed(metric === "lane_gd15" ? 0 : 2)), unit,
    t_score: t, percentile: pct, grade, radar: Math.round(Math.max(10, Math.min(100, pct))),
    baseline: cell ? { mean: cell.mean, std: cell.std, n: cell.n, position: cell.position, bucket: cell.bucket } : null,
  };
}

const GRADE_LABEL = {
  ko: { elite: "최상", high: "높음", avg: "보통", low: "낮음", risk: "위험" },
  en: { elite: "Elite", high: "High", avg: "Avg", low: "Low", risk: "Risk" },
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
    const w = REVIEW_RULES.swingFrames;
    let drop = null;
    let rise = null;
    for (let i = 0; i + w < gd.length; i++) {
      const d = gd[i + w].v - gd[i].v;
      if (!drop || d < drop.delta) drop = { from: gd[i].t, to: gd[i + w].t, delta: d, before: gd[i].v };
      if (!rise || d > rise.delta) rise = { from: gd[i].t, to: gd[i + w].t, delta: d, before: gd[i].v };
    }
    pat.max_drop_3m = drop ? -drop.delta : null;
    pat.max_rise_3m = rise ? rise.delta : null;
    pat._drop = drop;
    pat._rise = rise;
  }
  return { m, pat, me, opp, events };
}

function objName(o, ko) {
  if (o.type === "BUILDING_KILL") return o.buildingType === "INHIBITOR_BUILDING" ? (ko ? "억제기" : "Inhibitor") : (ko ? "포탑" : "Tower");
  const names = { BARON_NASHOR: ["바론", "Baron"], DRAGON: ["드래곤", "Dragon"], RIFTHERALD: ["전령", "Herald"], HORDE: ["공허 유충", "Voidgrubs"], ATAKHAN: ["아타칸", "Atakhan"] };
  return (names[o.monsterType] || [o.monsterType || "오브젝트", o.monsterType || "Objective"])[ko ? 0 : 1];
}

const LABELS = {
  lane: ["라인전", "Laning"], economy: ["경제", "Economy"], farm: ["파밍", "Farming"], combat: ["교전", "Combat"],
  vision: ["시야", "Vision"], frontline: ["탱킹", "Frontline"], survival: ["생존", "Survival"], damage: ["딜 기여", "Damage"],
};

/** Pure analysis. `baseline` = REVIEW_BASELINE (worker/review_baseline.js). */
export function analyzeMatch(match, timeline, puuid, lang, baseline) {
  const ko = lang === "ko";
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
    return {
      ...base, status: remake ? "remake" : "unsupported", metrics: [], costly_deaths: [], patterns: {}, turning_point: null,
      coaching: {
        summary: remake
          ? (ko ? `다시하기·조기 항복 경기(${fmtTime((info.gameDuration || 0) * 1000)})라 분석에서 제외했습니다.` : `Remake / early surrender (${fmtTime((info.gameDuration || 0) * 1000)}): excluded from analysis.`)
          : (ko ? "이 모드는 복기 지표를 지원하지 않습니다." : "This mode is not supported by the review metrics."),
        strengths: [], improvements: [], evidence: [],
      },
      findings: [], limitation: "",
    };
  }

  const pm = playerMetrics(match, timeline, me0.participantId);
  const { m, pat, me, opp, events } = pm;
  const bucket = durationBucket(m.durMin, baseline?.duration_edges?.[group === "other" ? "rift" : group]);
  const cell = (metric) => baselineCell(baseline, group, m.position, bucket, metric);
  const L = (k) => LABELS[k][ko ? 0 : 1];

  const laneMetric = group === "aram" || m.lane_gd15 === null ? "gold_per_min" : "lane_gd15";
  const defs = group === "aram"
    ? [["economy", "gold_per_min", "G/m"], ["farm", "cs_per_min", "CS/m"], ["combat", "kp", "%"],
      ["frontline", "taken_share", "%"], ["survival", "deaths_per10", "/10m"], ["damage", "dmg_share", "%"]]
    : [["lane", laneMetric, laneMetric === "lane_gd15" ? "G" : "G/m"], ["farm", "cs_per_min", "CS/m"], ["combat", "kp", "%"],
      ["vision", "vision_per_min", "/m"], ["survival", "deaths_per10", "/10m"], ["damage", "dmg_share", "%"]];
  const metrics = defs.map(([key, metric, unit]) => {
    const p = profile(key, L(key), metric, m[metric], unit, cell(metric));
    p.grade_label = GRADE_LABEL[ko ? "ko" : "en"][p.grade];
    return p;
  });

  // Costly deaths (existing §15 evidence)
  const myTeam = me.teamId;
  const myPid = me.participantId;
  const myWards = events.filter((e) => e.type === "WARD_PLACED" && e.creatorId === myPid);
  const enemyObjectives = events.filter((e) => (e.type === "ELITE_MONSTER_KILL" && e.killerTeamId && e.killerTeamId !== myTeam)
    || (e.type === "BUILDING_KILL" && e.teamId === myTeam));
  const costly = [];
  for (const d of events.filter((e) => e.type === "CHAMPION_KILL" && e.victimId === myPid)) {
    const ts = d.timestamp || 0;
    const lost = [...new Set(enemyObjectives.filter((o) => o.timestamp >= ts && o.timestamp <= ts + 60000).map((o) => objName(o, ko)))];
    const wardsPrior3m = myWards.filter((w) => w.timestamp >= ts - 180000 && w.timestamp <= ts).length;
    const attackers = 1 + (d.assistingParticipantIds?.length || 0);
    costly.push({
      time: fmtTime(ts), zone: group === "aram" ? (ko ? "칼바람 다리" : "ARAM bridge") : classifyZone(d.position?.x, d.position?.y, myTeam, lang),
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
  if (swing && swingZ !== undefined && swingZ >= 0 && group !== "aram") {
    const inWin = (e) => e.timestamp >= swing.from && e.timestamp <= swing.to;
    const myDeaths = events.filter((e) => e.type === "CHAMPION_KILL" && e.victimId === myPid && inWin(e));
    const objs = events.filter((e) => inWin(e) && ((e.type === "ELITE_MONSTER_KILL" && e.killerTeamId)
      || e.type === "BUILDING_KILL"));
    const ours = objs.filter((e) => (e.type === "ELITE_MONSTER_KILL" ? e.killerTeamId === myTeam : e.teamId !== myTeam)).map((e) => objName(e, ko));
    const theirs = objs.filter((e) => (e.type === "ELITE_MONSTER_KILL" ? e.killerTeamId !== myTeam : e.teamId === myTeam)).map((e) => objName(e, ko));
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
    if (p.t_score >= REVIEW_RULES.strengthT) {
      ev.push({ side: "strength", key: p.key, z: (p.t_score - 50) / 10, value: valTxt, text: ko
        ? `${p.label} ${valTxt} — 같은 포지션 기준 평균 ${ref}${p.unit}보다 좋습니다(T ${p.t_score}, 상위 ${(100 - p.percentile).toFixed(0)}%).`
        : `${p.label} ${valTxt} beat the same-role average ${ref}${p.unit} (T ${p.t_score}, top ${(100 - p.percentile).toFixed(0)}%).` });
    } else if (p.t_score < REVIEW_RULES.weaknessT) {
      ev.push({ side: "improvement", key: p.key, z: (p.t_score - 50) / 10, value: valTxt, text: ko
        ? `${p.label} ${valTxt} — 같은 포지션 기준 평균 ${ref}${p.unit}에 못 미쳤습니다(T ${p.t_score}, 하위 ${p.percentile.toFixed(0)}%).`
        : `${p.label} ${valTxt} fell short of the same-role average ${ref}${p.unit} (T ${p.t_score}, bottom ${p.percentile.toFixed(0)}%).` });
    }
  }
  const zs = REVIEW_RULES.strengthT / 10 - 5; // +0.5
  const zw = REVIEW_RULES.weaknessT / 10 - 5; // -0.5
  const patText = {
    lvl6_lead_sec: (v, good) => ko ? `레벨 6을 맞라인 상대보다 ${Math.abs(v).toFixed(0)}초 ${good ? "먼저" : "늦게"} 찍었습니다.` : `Hit level 6 ${Math.abs(v).toFixed(0)}s ${good ? "before" : "after"} your lane opponent.`,
    gank_deaths: (v) => ko ? `14분 전 상대 정글이 관여한 데스 ${v}회 — 정글 위치를 모를 때 라인 앞쪽에 섰던 시간이 길었습니다.` : `${v} deaths before 14:00 involving the enemy jungler.`,
    obj_vision_rate: (v, good) => ko ? `오브젝트 처치 60초 전 내 와드 설치율 ${v.toFixed(0)}%${good ? " — 오브젝트 시야 준비가 좋았습니다." : " — 오브젝트 전 시야 준비가 부족했습니다."}` : `Placed a ward in the 60s before ${v.toFixed(0)}% of epic objectives.`,
    power_play_rate: (v, good) => ko ? `바론·억제기 뒤 3분 안 포탑 전환율 ${v.toFixed(0)}%${good ? "." : " — 이득을 구조물로 바꾸지 못했습니다."}` : `${v.toFixed(0)}% of baron/inhibitor leads became structures within 3 min.`,
    first_death_fights: (v) => ko ? `진 한타 ${v}회에서 첫 데스였습니다 — 아군보다 먼저 들어가거나 먼저 잡혔습니다.` : `First to die in ${v} lost teamfight(s).`,
  };
  for (const [k, z] of Object.entries(patternZ)) {
    if (!patText[k]) continue;
    if (z >= zs && !(k === "gank_deaths" || k === "first_death_fights")) ev.push({ side: "strength", key: k, z, value: String(pat[k]), text: patText[k](pat[k], true) });
    else if (z <= zw && (k !== "gank_deaths" && k !== "first_death_fights" || pat[k] > 0)) ev.push({ side: "improvement", key: k, z, value: String(pat[k]), text: patText[k](pat[k], false) });
  }
  if (turning) {
    const amt = Math.abs(turning.team_gold_swing).toLocaleString("en-US");
    const deaths = turning.my_deaths.length ? (ko ? ` 이 구간 내 데스 ${turning.my_deaths.join("·")}.` : ` Your deaths: ${turning.my_deaths.join(", ")}.`) : "";
    const lostTxt = turning.objectives_lost.length ? (ko ? ` 잃은 것: ${turning.objectives_lost.join("·")}.` : ` Lost: ${turning.objectives_lost.join(", ")}.`) : "";
    const tookTxt = turning.objectives_taken.length ? (ko ? ` 얻은 것: ${turning.objectives_taken.join("·")}.` : ` Took: ${turning.objectives_taken.join(", ")}.`) : "";
    if (turning.favorable) {
      ev.push({ side: "strength", key: "turning_point", z: swingZ, value: String(turning.team_gold_swing), text: ko
        ? `분수령 ${turning.from}~${turning.to}: 팀 골드 차가 3분 동안 +${amt} 벌어졌습니다.${tookTxt}`
        : `Turning point ${turning.from}-${turning.to}: team gold lead grew by ${amt} in 3 min.${tookTxt}` });
    } else {
      ev.push({ side: "improvement", key: "turning_point", z: -swingZ, value: String(turning.team_gold_swing), text: ko
        ? `분수령 ${turning.from}~${turning.to}: 팀 골드 차가 3분 동안 -${amt} 무너졌습니다.${deaths}${lostTxt}`
        : `Turning point ${turning.from}-${turning.to}: team gold fell by ${amt} in 3 min.${deaths}${lostTxt}` });
    }
  }
  const strengthsEv = ev.filter((e) => e.side === "strength").sort((a, b) => b.z - a.z).slice(0, REVIEW_RULES.maxItems);
  // The turning point always leads the improvements (AGENTS §19-6); the rest by z, worst first.
  const improvEv = ev.filter((e) => e.side === "improvement")
    .sort((a, b) => (b.key === "turning_point") - (a.key === "turning_point") || a.z - b.z).slice(0, REVIEW_RULES.maxItems);
  const strengths = strengthsEv.length ? strengthsEv.map((e) => e.text)
    : [ko ? "기준 분포에서 평균보다 뚜렷하게(T 55 이상) 앞선 지표가 없습니다." : "No metric clearly above the baseline (T >= 55)."];
  const improvements = improvEv.length ? improvEv.map((e) => e.text)
    : [ko ? "기준 분포에서 평균보다 뚜렷하게(T 45 미만) 뒤처진 지표가 없습니다." : "No metric clearly below the baseline (T < 45)."];

  const oppName = opp?.championName || (ko ? "맞라인 상대 없음" : "no lane opponent");
  const durTxt = fmtTime((info.gameDuration || 0) * 1000);
  const top = strengthsEv[0];
  const low = improvEv[0];
  const summary = ko
    ? `${me.championName}${group === "aram" ? "" : ` vs ${oppName}`} (${me.win ? "승리" : "패배"}, ${durTxt})${top ? ` — 강점: ${LABELS[top.key]?.[0] || "패턴"}` : ""}${low ? ` · 우선 고칠 점: ${low.key === "turning_point" ? `${turning.from}~${turning.to} 분수령` : (LABELS[low.key]?.[0] || "패턴")}` : ""}.`
    : `${me.championName}${group === "aram" ? "" : ` vs ${oppName}`} (${me.win ? "Win" : "Loss"}, ${durTxt})${top ? ` — strength: ${LABELS[top.key]?.[1] || "pattern"}` : ""}${low ? ` · fix first: ${low.key === "turning_point" ? `${turning.from}-${turning.to} turning point` : (LABELS[low.key]?.[1] || "pattern")}` : ""}.`;

  const findings = [];
  const csm = m.cs_per_min.toFixed(1);
  const kpTxt = m.kp === null ? "-" : `${Math.round(m.kp)}%`;
  findings.push(m.lane_gd15 !== null
    ? (ko ? `15분 맞라인 골드 차이: ${m.lane_gd15 >= 0 ? "+" : ""}${m.lane_gd15}G (분당 CS ${csm}, 킬관여 ${kpTxt}, 시야 점수 ${me.visionScore || 0}).`
      : `15m lane gold diff: ${m.lane_gd15 >= 0 ? "+" : ""}${m.lane_gd15}G (${csm} CS/m, ${kpTxt} KP, ${me.visionScore || 0} vision).`)
    : (ko ? `경기 요약: 분당 CS ${csm}, 킬관여 ${kpTxt}, 시야 점수 ${me.visionScore || 0}.` : `Match summary: ${csm} CS/m, ${kpTxt} KP, ${me.visionScore || 0} vision.`));
  for (const d of topDeaths) {
    const objTxt = d.objectives_lost_60s.length ? (ko ? ` → 사망 후 60초 내 ${d.objectives_lost_60s.join("·")} 손실` : ` → lost ${d.objectives_lost_60s.join(", ")} within 60s`) : "";
    const wardTxt = group === "aram" ? "" : (ko ? ` (직전 3분 와드 ${d.wards_prior_3m}개)` : ` (${d.wards_prior_3m} wards in prior 3m)`);
    findings.push(ko ? `${d.time} ${d.zone}에서 ${d.attackers}명에게 사망${objTxt}${wardTxt}.` : `${d.time} died in ${d.zone} vs ${d.attackers}${objTxt}${wardTxt}.`);
  }
  if (!topDeaths.length) findings.push(ko ? "기록된 사망이 없거나 타임라인 데이터가 없는 경기입니다." : "No deaths recorded or timeline unavailable.");

  const { _drop, _rise, ...patterns } = pat;
  const sample = metrics.find((p) => p.baseline)?.baseline;
  return {
    ...base, status: "ok",
    cs_per_min: Number(m.cs_per_min.toFixed(1)), vision_score: me.visionScore || 0,
    kill_participation: m.kp === null ? 0 : Math.round(m.kp), gold_diff_15m: m.lane_gd15,
    costly_deaths: topDeaths, metrics, patterns, pattern_z: patternZ, turning_point: turning,
    baseline: { queue: group === "other" ? "rift" : group, position: m.position, duration_bucket: bucket, source: baseline?.source || null },
    coaching: { summary, strengths, improvements, evidence: [...strengthsEv, ...improvEv].map(({ text, ...rest }) => rest) },
    findings,
    limitation: ko
      ? `근거 범위: Riot 공식 경기·1분 타임라인 수치를 ${group === "aram" ? "칼바람" : "솔로 랭크 협곡"} 실제 경기 분포(포지션·경기 길이별${sample ? `, 표본 ${sample.n}명` : ""})와 비교한 T점수(50+10z)입니다. 티어별 분포가 아니며, 스킬 적중·상대 와드·팀 콜은 데이터에 없어 단정하지 않습니다.`
      : `Scope: T-scores (50+10z) against real ${group === "aram" ? "ARAM" : "ranked Summoner's Rift"} matches by role and game length${sample ? ` (n=${sample.n})` : ""}. Not tier-specific; skillshots, enemy wards and comms are not in the data.`,
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
