/**
 * AGENTS §15: Real OAuth 2.0 Social Login (Google & Discord) on Cloudflare Workers ($0).
 *
 * - Zero passwords stored; only SHA-256(provider + ":" + sub + salt) is persisted in D1 `users`.
 * - CSRF protection via HMAC-signed short-lived `state` parameter.
 * - Redirects back to the allowed frontend origin using `#auth_token=<token>` so the token
 *   is never sent to GitHub Pages server logs and is immediately stripped by client JS.
 */

import { randomChampNickname, sha256Hex, signToken, verifyStateToken } from "./review.js";

const ALLOWED_RETURN_ORIGINS = new Set([
  "https://wpalswpa.github.io",
  "http://127.0.0.1:8787",
  "http://localhost:8787",
]);

function json(body, status, headers) {
  return new Response(JSON.stringify(body), { status, headers });
}

export function handleAuthProviders(env, headers) {
  return json({
    google: Boolean(env.GOOGLE_CLIENT_ID && env.GOOGLE_CLIENT_SECRET),
    discord: Boolean(env.DISCORD_CLIENT_ID && env.DISCORD_CLIENT_SECRET),
  }, 200, headers);
}

export async function handleOAuthStart(provider, request, env, headers) {
  const url = new URL(request.url);
  if (url.searchParams.get("consent") !== "1") {
    return json({ error: "consent_required" }, 400, headers);
  }
  const returnOrigin = url.searchParams.get("origin") || "https://wpalswpa.github.io";
  const safeOrigin = ALLOWED_RETURN_ORIGINS.has(returnOrigin) ? returnOrigin : "https://wpalswpa.github.io";
  const returnPath = safeOrigin === "https://wpalswpa.github.io" ? "/lol-pacemaker/" : "/";
  const callbackUrl = `${url.origin}/v1/auth/oauth/${provider}/callback`;

  const state = await signToken({
    kind: "oauth_state",
    provider,
    return_to: safeOrigin + returnPath,
    exp: Math.floor(Date.now() / 1000) + 600,
  }, env);

  if (provider === "google") {
    if (!env.GOOGLE_CLIENT_ID || !env.GOOGLE_CLIENT_SECRET) {
      return json({ error: "google_oauth_not_configured" }, 503, headers);
    }
    const authUrl = new URL("https://accounts.google.com/o/oauth2/v2/auth");
    authUrl.searchParams.set("client_id", env.GOOGLE_CLIENT_ID);
    authUrl.searchParams.set("redirect_uri", callbackUrl);
    authUrl.searchParams.set("response_type", "code");
    authUrl.searchParams.set("scope", "openid profile");
    authUrl.searchParams.set("state", state);
    authUrl.searchParams.set("prompt", "select_account");
    return Response.redirect(authUrl.toString(), 302);
  }

  if (provider === "discord") {
    if (!env.DISCORD_CLIENT_ID || !env.DISCORD_CLIENT_SECRET) {
      return json({ error: "discord_oauth_not_configured" }, 503, headers);
    }
    const authUrl = new URL("https://discord.com/api/oauth2/authorize");
    authUrl.searchParams.set("client_id", env.DISCORD_CLIENT_ID);
    authUrl.searchParams.set("redirect_uri", callbackUrl);
    authUrl.searchParams.set("response_type", "code");
    authUrl.searchParams.set("scope", "identify");
    authUrl.searchParams.set("state", state);
    return Response.redirect(authUrl.toString(), 302);
  }

  return json({ error: "unsupported_provider" }, 400, headers);
}

export async function handleOAuthCallback(provider, request, env, headers) {
  const url = new URL(request.url);
  const code = url.searchParams.get("code") || "";
  const stateStr = url.searchParams.get("state") || "";
  const state = await verifyStateToken(stateStr, env);
  if (!state || state.kind !== "oauth_state" || state.provider !== provider) {
    return json({ error: "invalid_oauth_state" }, 400, headers);
  }
  if (!code) {
    return Response.redirect(`${state.return_to}#auth_error=cancelled`, 302);
  }

  const callbackUrl = `${url.origin}/v1/auth/oauth/${provider}/callback`;
  let providerSub = "";
  let avatarUrl = "";

  try {
    if (provider === "google") {
      const tokRes = await fetch("https://oauth2.googleapis.com/token", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({
          code,
          client_id: env.GOOGLE_CLIENT_ID,
          client_secret: env.GOOGLE_CLIENT_SECRET,
          redirect_uri: callbackUrl,
          grant_type: "authorization_code",
        }),
      });
      if (!tokRes.ok) return Response.redirect(`${state.return_to}#auth_error=token_exchange`, 302);
      const tok = await tokRes.json();
      const userRes = await fetch("https://openidconnect.googleapis.com/v1/userinfo", {
        headers: { Authorization: `Bearer ${tok.access_token}` },
      });
      if (!userRes.ok) return Response.redirect(`${state.return_to}#auth_error=userinfo`, 302);
      const u = await userRes.json();
      providerSub = String(u.sub || "");
      avatarUrl = String(u.picture || "");
    } else if (provider === "discord") {
      const tokRes = await fetch("https://discord.com/api/oauth2/token", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({
          code,
          client_id: env.DISCORD_CLIENT_ID,
          client_secret: env.DISCORD_CLIENT_SECRET,
          redirect_uri: callbackUrl,
          grant_type: "authorization_code",
        }),
      });
      if (!tokRes.ok) return Response.redirect(`${state.return_to}#auth_error=token_exchange`, 302);
      const tok = await tokRes.json();
      const userRes = await fetch("https://discord.com/api/users/@me", {
        headers: { Authorization: `Bearer ${tok.access_token}` },
      });
      if (!userRes.ok) return Response.redirect(`${state.return_to}#auth_error=userinfo`, 302);
      const u = await userRes.json();
      providerSub = String(u.id || "");
      avatarUrl = u.avatar ? `https://cdn.discordapp.com/avatars/${u.id}/${u.avatar}.png?size=128` : "";
    }
  } catch {
    return Response.redirect(`${state.return_to}#auth_error=oauth_failed`, 302);
  }

  if (!providerSub || !env.DB) {
    return Response.redirect(`${state.return_to}#auth_error=missing_sub`, 302);
  }

  const subHash = await sha256Hex(`${provider}:${providerSub}:${env.SUPPORT_SALT || ""}`);
  const existing = await env.DB.prepare("SELECT * FROM users WHERE provider_sub_hash = ?").bind(subHash).first();
  const nowIso = new Date().toISOString();
  const day = nowIso.slice(0, 10);
  let userId = existing?.user_id;

  let isNew = false;
  if (!existing) {
    isNew = true;
    userId = crypto.randomUUID();
    const initNick = randomChampNickname();
    await env.DB.prepare(
      "INSERT INTO users (user_id, provider, provider_sub_hash, display_name, avatar_url, consent_version, consented_at, created_day) " +
      "VALUES (?, ?, ?, ?, ?, '1', ?, ?)",
    ).bind(userId, provider, subHash, initNick, avatarUrl, nowIso, day).run();
  } else if (
    avatarUrl &&
    avatarUrl !== existing.avatar_url &&
    !(existing.profile_icon_id && existing.profile_icon_id > 0) &&
    !String(existing.avatar_url || "").startsWith("data:image/")
  ) {
    await env.DB.prepare("UPDATE users SET avatar_url = ? WHERE user_id = ?").bind(avatarUrl, userId).run();
  }

  const sessionToken = await signToken({
    user_id: userId,
    provider,
    exp: Math.floor(Date.now() / 1000) + 30 * 24 * 3600,
  }, env);

  const extra = isNew ? "&onboarding=1" : "";
  return Response.redirect(`${state.return_to}#auth_token=${encodeURIComponent(sessionToken)}${extra}`, 302);
}
