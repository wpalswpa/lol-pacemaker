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
  const participants = match.info?.participants || [];
  const me = participants.find((p) => p.puuid === myPuuid);
  if (!me) return json({ error: "player_not_in_match" }, 404, headers);

  const myPid = me.participantId;
  const myTeam = me.teamId;
  const durationMin = Math.max(1, (match.info.gameDuration || 60) / 60);
  const myCs = (me.totalMinionsKilled || 0) + (me.neutralMinionsKilled || 0);
  const teamKills = Math.max(1, participants.filter((p) => p.teamId === myTeam).reduce((a, p) => a + (p.kills || 0), 0));
  const kp = Math.round(((me.kills + me.assists) / teamKills) * 100);

  let goldDiff15 = null;
  const costlyDeaths = [];
  if (tlRes.ok) {
    const tl = await tlRes.json();
    const frames = tl.info?.frames || [];
    const f15 = frames[Math.min(15, frames.length - 1)];
    if (f15?.participantFrames?.[String(myPid)]) {
      const opp = participants.find((p) => p.teamId !== myTeam && p.teamPosition && p.teamPosition === me.teamPosition);
      const myGold = f15.participantFrames[String(myPid)].totalGold || 0;
      if (opp && f15.participantFrames[String(opp.participantId)]) {
        goldDiff15 = myGold - (f15.participantFrames[String(opp.participantId)].totalGold || 0);
      }
    }

    const allEvents = frames.flatMap((f) => f.events || []);
    const myWards = allEvents.filter((e) => e.type === "WARD_PLACED" && e.creatorId === myPid);
    const enemyObjectives = allEvents.filter((e) => {
      if (e.type === "ELITE_MONSTER_KILL") return e.killerTeamId && e.killerTeamId !== myTeam;
      if (e.type === "BUILDING_KILL") return e.teamId === myTeam;
      return false;
    });

    const myDeaths = allEvents.filter((e) => e.type === "CHAMPION_KILL" && e.victimId === myPid);
    for (const d of myDeaths) {
      const ts = d.timestamp || 0;
      const lostObjs = enemyObjectives
        .filter((o) => o.timestamp >= ts && o.timestamp <= ts + 60000)
        .map((o) => {
          if (o.type === "BUILDING_KILL") return lang === "ko" ? "포탑" : "Tower";
          if (o.monsterType === "BARON_NASHOR") return lang === "ko" ? "바론" : "Baron";
          if (o.monsterType === "DRAGON") return lang === "ko" ? "드래곤" : "Dragon";
          if (o.monsterType === "RIFTHERALD") return lang === "ko" ? "전령" : "Herald";
          if (o.monsterType === "HORDE") return lang === "ko" ? "공허 유충" : "Voidgrubs";
          return o.monsterType || "Objective";
        });
      const wardsPrior3m = myWards.filter((w) => w.timestamp >= ts - 180000 && w.timestamp <= ts).length;
      const attackers = 1 + (d.assistingParticipantIds?.length || 0);
      const score = lostObjs.length * 100 + (ts > 900000 ? 30 : 10) + (wardsPrior3m === 0 ? 15 : 0) + attackers * 5;
      costlyDeaths.push({
        time: fmtTime(ts),
        zone: classifyZone(d.position?.x, d.position?.y, myTeam, lang),
        attackers,
        objectives_lost_60s: [...new Set(lostObjs)],
        wards_prior_3m: wardsPrior3m,
        _score: score,
      });
    }
    costlyDeaths.sort((a, b) => b._score - a._score);
  }

  const topDeaths = costlyDeaths.slice(0, 3).map(({ _score, ...rest }) => rest);
  const findings = [];
  const ko = lang === "ko";
  if (goldDiff15 !== null) {
    const sign = goldDiff15 >= 0 ? "+" : "";
    findings.push(ko
      ? `15분 맞라인 골드 차이: ${sign}${goldDiff15}G (분당 CS ${(myCs / durationMin).toFixed(1)}, 킬관여 ${kp}%, 시야 점수 ${me.visionScore}).`
      : `15m lane gold diff: ${sign}${goldDiff15}G (${(myCs / durationMin).toFixed(1)} CS/m, ${kp}% KP, ${me.visionScore} vision).`);
  } else {
    findings.push(ko
      ? `경기 요약: 분당 CS ${(myCs / durationMin).toFixed(1)}, 킬관여 ${kp}%, 시야 점수 ${me.visionScore}.`
      : `Match summary: ${(myCs / durationMin).toFixed(1)} CS/m, ${kp}% KP, ${me.visionScore} vision.`);
  }
  for (const d of topDeaths) {
    const objTxt = d.objectives_lost_60s.length
      ? (ko ? ` → 사망 후 60초 내 ${d.objectives_lost_60s.join("·")} 손실` : ` → lost ${d.objectives_lost_60s.join(", ")} within 60s`)
      : "";
    const wardTxt = d.wards_prior_3m === 0
      ? (ko ? " (직전 3분 와드 0개)" : " (0 wards in prior 3m)")
      : (ko ? ` (직전 3분 와드 ${d.wards_prior_3m}개)` : ` (${d.wards_prior_3m} wards in prior 3m)`);
    findings.push(ko
      ? `${d.time} ${d.zone}에서 ${d.attackers}명에게 사망${objTxt}${wardTxt}.`
      : `${d.time} died in ${d.zone} vs ${d.attackers}${objTxt}${wardTxt}.`);
  }
  if (!topDeaths.length) {
    findings.push(ko ? "기록된 사망이 없거나 타임라인 데이터가 없는 경기입니다." : "No deaths recorded or timeline unavailable.");
  }

  return json({
    match_id: matchId,
    riot_id: myRiotId,
    champion: me.championName,
    win: Boolean(me.win),
    kda: `${me.kills}/${me.deaths}/${me.assists}`,
    cs_per_min: Number((myCs / durationMin).toFixed(1)),
    vision_score: me.visionScore || 0,
    kill_participation: kp,
    gold_diff_15m: goldDiff15,
    costly_deaths: topDeaths,
    findings,
    limitation: ko
      ? "근거 범위: Riot 공식 1분 간격 타임라인 데이터만 사용합니다. 스킬 적중·상대 와드·팀 콜은 데이터에 없어 단정하지 않습니다."
      : "Scope: Based strictly on Riot's 1-minute timeline data. Skillshots, enemy vision, and voice comms are not in the data.",
  }, 200, headers);
}
