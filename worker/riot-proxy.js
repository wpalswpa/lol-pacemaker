/**
 * LoL Pacemaker — $0 Serverless Riot Match History, Auth & AI Review Proxy (Cloudflare Workers)
 *
 * Security:
 * - RIOT_API_KEY lives ONLY in Cloudflare Worker Secrets (`wrangler secret put RIOT_API_KEY`)
 * - Strict CORS allowlist (only https://wpalswpa.github.io + local dev)
 * - 60s Cloudflare edge cache per Riot ID to protect rate limits
 */

import { handleGames } from "./games.js";
import { handleSupport } from "./support.js";
import { handleChat } from "./chat.js";
import {
  PLATFORM_HOST,
  REGION_HOST,
  handleAuthChallenge,
  handleAuthVerify,
  handleAuthMe,
  handleAccount,
  handleReview,
} from "./review.js";
import {
  handleAuthProviders,
  handleOAuthStart,
  handleOAuthCallback,
} from "./oauth.js";

const ALLOWED_ORIGINS = new Set([
  "https://wpalswpa.github.io",
  "http://127.0.0.1:8787",
  "http://localhost:8787",
]);

const DDRAGON_VER = "16.23.1";

function corsHeaders(origin) {
  const allow = ALLOWED_ORIGINS.has(origin) ? origin : "https://wpalswpa.github.io";
  return {
    "Access-Control-Allow-Origin": allow,
    "Access-Control-Allow-Methods": "GET, POST, PATCH, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
    "Content-Type": "application/json; charset=utf-8",
    "X-Content-Type-Options": "nosniff",
  };
}

export default {
  async fetch(request, env) {
    const origin = request.headers.get("Origin") || "";
    const headers = corsHeaders(origin);
    const url = new URL(request.url);
    const path = url.pathname;

    // AGENTS §14: desktop-app contribution route (no browser CORS needed)
    if (path === "/v1/games") return handleGames(request, env);

    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers });

    // AGENTS §16: support chatbot & in-site contact
    if (path === "/v1/chat") return handleChat(request, env, headers);
    if (path === "/v1/support") return handleSupport(request, env, headers);

    // AGENTS §15: Real OAuth (Google / Discord), Riot account ownership link, member account & self-only AI review
    if (path === "/v1/auth/providers") return handleAuthProviders(env, headers);
    if (path === "/v1/auth/oauth/google/start") return handleOAuthStart("google", request, env, headers);
    if (path === "/v1/auth/oauth/google/callback") return handleOAuthCallback("google", request, env, headers);
    if (path === "/v1/auth/oauth/discord/start") return handleOAuthStart("discord", request, env, headers);
    if (path === "/v1/auth/oauth/discord/callback") return handleOAuthCallback("discord", request, env, headers);
    if (path === "/v1/auth/challenge") return handleAuthChallenge(request, env, headers);
    if (path === "/v1/auth/verify") return handleAuthVerify(request, env, headers);
    if (path === "/v1/auth/me") return handleAuthMe(request, env, headers);
    if (path === "/v1/account") return handleAccount(request, env, headers);
    if (path === "/v1/review") return handleReview(request, env, headers);

    if (request.method !== "GET") {
      return new Response(JSON.stringify({ error: "method_not_allowed" }), { status: 405, headers });
    }
    if (!env.RIOT_API_KEY) {
      return new Response(JSON.stringify({ error: "riot_key_not_configured" }), { status: 503, headers });
    }

    const riotId = (url.searchParams.get("riot_id") || "").trim();
    const region = (url.searchParams.get("region") || "kr").toLowerCase();
    const host = REGION_HOST[region] || REGION_HOST.kr;
    const plat = PLATFORM_HOST[region] || PLATFORM_HOST.kr;

    if (!riotId.includes("#")) {
      return new Response(JSON.stringify({ error: "invalid_riot_id_format" }), { status: 400, headers });
    }
    const [rawName, rawTag] = riotId.split("#", 2);
    const gameName = rawName.trim();
    const tagLine = rawTag.trim();
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

    // Fetch summoner profile, ranked tiers, and recent 10 match IDs in parallel
    const [sumRes, rankRes, idsRes] = await Promise.all([
      fetch(`https://${plat}/lol/summoner/v4/summoners/by-puuid/${encodeURIComponent(account.puuid)}`, { headers: riotHeaders, cf: { cacheTtl: 60 } }),
      fetch(`https://${plat}/lol/league/v4/entries/by-puuid/${encodeURIComponent(account.puuid)}`, { headers: riotHeaders, cf: { cacheTtl: 60 } }),
      fetch(`https://${host}/lol/match/v5/matches/by-puuid/${encodeURIComponent(account.puuid)}/ids?start=0&count=10`, { headers: riotHeaders, cf: { cacheTtl: 60 } }),
    ]);

    if (!idsRes.ok) {
      return new Response(JSON.stringify({ error: "match_ids_failed" }), { status: idsRes.status, headers });
    }
    const matchIds = await idsRes.json();
    const sumData = sumRes.ok ? await sumRes.json() : {};
    const rankEntries = rankRes.ok ? await rankRes.json() : [];
    const pickRank = (q) => {
      const r = Array.isArray(rankEntries) ? rankEntries.find((e) => e.queueType === q) : null;
      return r ? { tier: r.tier, rank: r.rank, lp: r.leaguePoints, wins: r.wins, losses: r.losses } : null;
    };

    const matchDetails = await Promise.all(
      matchIds.slice(0, 10).map(async (id) => {
        const mRes = await fetch(`https://${host}/lol/match/v5/matches/${encodeURIComponent(id)}`, {
          headers: riotHeaders,
          cf: { cacheTtl: 300 },
        });
        if (!mRes.ok) return null;
        const m = await mRes.json();
        const parts = m.info?.participants || [];
        const me = parts.find((p) => p.puuid === account.puuid);
        if (!me) return null;

        const teamKills = Math.max(1, parts.filter((p) => p.teamId === me.teamId).reduce((a, p) => a + (p.kills || 0), 0));
        const maxDmg = Math.max(1, ...parts.map((p) => p.totalDamageDealtToChampions || 0));
        return {
          matchId: id,
          queueId: m.info.queueId || 0,
          gameMode: m.info.gameMode,
          durationSec: m.info.gameDuration || 0,
          durationMin: Math.round((m.info.gameDuration || 0) / 60),
          gameEndTimestamp: m.info.gameEndTimestamp || 0,
          champion: me.championName,
          champLevel: me.champLevel || 1,
          win: Boolean(me.win),
          kills: me.kills,
          deaths: me.deaths,
          assists: me.assists,
          cs: (me.totalMinionsKilled || 0) + (me.neutralMinionsKilled || 0),
          position: me.teamPosition || "",
          killParticipation: Math.round(((me.kills + me.assists) / teamKills) * 100),
          damageDealt: me.totalDamageDealtToChampions || 0,
          wardsPlaced: me.wardsPlaced || 0,
          items: [me.item0, me.item1, me.item2, me.item3, me.item4, me.item5, me.item6].map((x) => x || 0),
          participants: parts.map((p) => ({
            teamId: p.teamId,
            win: Boolean(p.win),
            champion: p.championName,
            champLevel: p.champLevel || 1,
            riotId: p.riotIdGameName ? `${p.riotIdGameName}` : (p.summonerName || ""),
            kills: p.kills,
            deaths: p.deaths,
            assists: p.assists,
            cs: (p.totalMinionsKilled || 0) + (p.neutralMinionsKilled || 0),
            damage: p.totalDamageDealtToChampions || 0,
            damagePct: Math.round(((p.totalDamageDealtToChampions || 0) / maxDmg) * 100),
            wards: p.wardsPlaced || 0,
            items: [p.item0, p.item1, p.item2, p.item3, p.item4, p.item5, p.item6].map((x) => x || 0),
            isMe: p.puuid === account.puuid,
          })),
        };
      }),
    );

    return new Response(
      JSON.stringify({
        riot_id: `${account.gameName}#${account.tagLine}`,
        region: region.toUpperCase(),
        ddragonVersion: DDRAGON_VER,
        profile: {
          level: sumData.summonerLevel || 0,
          profileIconId: sumData.profileIconId || 0,
          soloRank: pickRank("RANKED_SOLO_5x5"),
          flexRank: pickRank("RANKED_FLEX_SR"),
        },
        matches: matchDetails.filter(Boolean),
      }),
      { status: 200, headers: { ...headers, "Cache-Control": "public, max-age=60" } },
    );
  },
};
