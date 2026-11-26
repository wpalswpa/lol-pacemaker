/**
 * LoL Pacemaker — $0 Serverless Riot Match History Proxy (Cloudflare Workers)
 *
 * Security:
 * - RIOT_API_KEY lives ONLY in Cloudflare Worker Secrets (`wrangler secret put RIOT_API_KEY`)
 * - Strict CORS allowlist (only https://wpalswpa.github.io)
 * - 60s Cloudflare edge cache per Riot ID to protect rate limits
 * - Supports global routing: asia (KR/JP), americas (NA/BR/LAN/LAS), europe (EUW/EUNE/TR), sea (OC/SG/VN)
 */

import { handleGames } from "./games.js";

const ALLOWED_ORIGINS = new Set([
  "https://wpalswpa.github.io",
  "http://127.0.0.1:8787",
  "http://localhost:8787",
]);

const REGION_HOST = {
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

function corsHeaders(origin) {
  const allow = ALLOWED_ORIGINS.has(origin) ? origin : "https://wpalswpa.github.io";
  return {
    "Access-Control-Allow-Origin": allow,
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Content-Type": "application/json; charset=utf-8",
    "X-Content-Type-Options": "nosniff",
  };
}

export default {
  async fetch(request, env) {
    const origin = request.headers.get("Origin") || "";
    const headers = corsHeaders(origin);

    // AGENTS §14: desktop-app contribution route (no browser CORS needed)
    if (new URL(request.url).pathname === "/v1/games") {
      return handleGames(request, env);
    }

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers });
    }
    if (request.method !== "GET") {
      return new Response(JSON.stringify({ error: "method_not_allowed" }), { status: 405, headers });
    }
    if (!env.RIOT_API_KEY) {
      return new Response(JSON.stringify({ error: "riot_key_not_configured" }), { status: 503, headers });
    }

    const url = new URL(request.url);
    const riotId = (url.searchParams.get("riot_id") || "").trim();
    const region = (url.searchParams.get("region") || "kr").toLowerCase();
    const host = REGION_HOST[region] || REGION_HOST.kr;

    if (!riotId.includes("#")) {
      return new Response(JSON.stringify({ error: "invalid_riot_id_format" }), { status: 400, headers });
    }
    const [gameName, tagLine] = riotId.split("#", 2);
    if (!gameName || !tagLine || gameName.length > 32 || tagLine.length > 10) {
      return new Response(JSON.stringify({ error: "invalid_riot_id_length" }), { status: 400, headers });
    }

    const riotHeaders = { "X-Riot-Token": env.RIOT_API_KEY };
    const accUrl = `https://${host}/riot/account/v1/accounts/by-riot-id/${encodeURIComponent(gameName)}/${encodeURIComponent(tagLine)}`;
    const accRes = await fetch(accUrl, { headers: riotHeaders, cf: { cacheTtl: 60 } });
    if (!accRes.ok) {
      return new Response(JSON.stringify({ error: "summoner_not_found", status: accRes.status }), { status: accRes.status, headers });
    }
    const account = await accRes.json();

    const idsUrl = `https://${host}/lol/match/v5/matches/by-puuid/${encodeURIComponent(account.puuid)}/ids?start=0&count=5`;
    const idsRes = await fetch(idsUrl, { headers: riotHeaders, cf: { cacheTtl: 60 } });
    if (!idsRes.ok) {
      return new Response(JSON.stringify({ error: "match_ids_failed" }), { status: idsRes.status, headers });
    }
    const matchIds = await idsRes.json();

    const matches = [];
    for (const id of matchIds.slice(0, 5)) {
      const mRes = await fetch(`https://${host}/lol/match/v5/matches/${encodeURIComponent(id)}`, {
        headers: riotHeaders,
        cf: { cacheTtl: 300 },
      });
      if (!mRes.ok) continue;
      const m = await mRes.json();
      const me = (m.info?.participants || []).find((p) => p.puuid === account.puuid);
      if (!me) continue;
      matches.push({
        matchId: id,
        gameMode: m.info.gameMode,
        durationMin: Math.round((m.info.gameDuration || 0) / 60),
        champion: me.championName,
        win: Boolean(me.win),
        kills: me.kills,
        deaths: me.deaths,
        assists: me.assists,
        cs: (me.totalMinionsKilled || 0) + (me.neutralMinionsKilled || 0),
      });
    }

    return new Response(
      JSON.stringify({
        riot_id: `${account.gameName}#${account.tagLine}`,
        region: region.toUpperCase(),
        matches,
      }),
      { status: 200, headers: { ...headers, "Cache-Control": "public, max-age=60" } },
    );
  },
};
