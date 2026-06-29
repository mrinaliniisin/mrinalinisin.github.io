// Cloudflare Worker: subscription store + Web Push broadcaster for
// mrinalinisin.github.io. Deployed separately from the static site (GitHub
// Pages can't run code). See README.md for setup.
//
// Endpoints:
//   POST /subscribe    {PushSubscription}              -> store in KV
//   POST /unsubscribe  {endpoint}                      -> remove from KV
//   GET  /latest                                       -> last broadcast {title,body,url}
//   POST /broadcast    {title,body,url}  (Bearer auth) -> send empty push to all subs
//
// Pushes are sent WITHOUT an encrypted payload (only a VAPID JWT). The service
// worker fetches /latest to fill in the notification text.

const enc = new TextEncoder();

const b64url = buf => btoa(String.fromCharCode(...new Uint8Array(buf)))
  .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const b64urlStr = s => btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const bytesFromB64 = b64 => Uint8Array.from(atob(b64), c => c.charCodeAt(0));

let signingKey;  // cached across requests in the same isolate
async function vapidKey(env) {
  if (!signingKey) {
    signingKey = await crypto.subtle.importKey(
      "pkcs8", bytesFromB64(env.VAPID_PRIVATE),
      { name: "ECDSA", namedCurve: "P-256" }, false, ["sign"]);
  }
  return signingKey;
}

// Build the per-endpoint `Authorization: vapid t=<jwt>, k=<pubkey>` header.
async function vapidAuth(env, endpoint) {
  const header = b64urlStr(JSON.stringify({ typ: "JWT", alg: "ES256" }));
  const claims = b64urlStr(JSON.stringify({
    aud: new URL(endpoint).origin,                 // push-service origin
    exp: Math.floor(Date.now() / 1000) + 12 * 3600,
    sub: env.VAPID_SUBJECT,
  }));
  const sig = await crypto.subtle.sign(
    { name: "ECDSA", hash: "SHA-256" }, await vapidKey(env),
    enc.encode(header + "." + claims));
  return `vapid t=${header}.${claims}.${b64url(sig)}, k=${env.VAPID_PUBLIC}`;
}

function cors(env, extra = {}) {
  return {
    "Access-Control-Allow-Origin": env.ALLOW_ORIGIN || "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    ...extra,
  };
}
const json = (env, status, obj) => new Response(JSON.stringify(obj), {
  status, headers: { "Content-Type": "application/json", ...cors(env) },
});
const subKey = async endpoint =>
  "sub:" + b64url(await crypto.subtle.digest("SHA-256", enc.encode(endpoint)));

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "OPTIONS") return new Response(null, { headers: cors(env) });

    if (request.method === "POST" && url.pathname === "/subscribe") {
      const sub = await request.json().catch(() => null);
      if (!sub || !sub.endpoint) return json(env, 400, { error: "bad subscription" });
      await env.SUBS.put(await subKey(sub.endpoint), JSON.stringify(sub));
      return json(env, 201, { ok: true });
    }

    if (request.method === "POST" && url.pathname === "/unsubscribe") {
      const { endpoint } = await request.json().catch(() => ({}));
      if (endpoint) await env.SUBS.delete(await subKey(endpoint));
      return json(env, 200, { ok: true });
    }

    if (request.method === "GET" && url.pathname === "/latest") {
      const latest = await env.SUBS.get("latest");
      return json(env, 200, latest ? JSON.parse(latest) : {});
    }

    if (request.method === "POST" && url.pathname === "/broadcast") {
      if (request.headers.get("Authorization") !== "Bearer " + env.BROADCAST_SECRET) {
        return json(env, 401, { error: "unauthorized" });
      }
      const msg = await request.json().catch(() => ({}));
      await env.SUBS.put("latest", JSON.stringify({
        title: msg.title || "Something new is up",
        body: msg.body || "", url: msg.url || "/", ts: Date.now(),
      }));

      let sent = 0, pruned = 0, failed = 0, cursor;
      do {
        const list = await env.SUBS.list({ prefix: "sub:", cursor });
        for (const k of list.keys) {
          const sub = JSON.parse(await env.SUBS.get(k.name));
          try {
            const res = await fetch(sub.endpoint, {
              method: "POST",
              headers: { TTL: "2419200", Authorization: await vapidAuth(env, sub.endpoint) },
            });
            if (res.status === 404 || res.status === 410) { await env.SUBS.delete(k.name); pruned++; }
            else if (res.ok) sent++;
            else failed++;
          } catch { failed++; }
        }
        cursor = list.list_complete ? null : list.cursor;
      } while (cursor);
      return json(env, 200, { ok: true, sent, pruned, failed });
    }

    if (url.pathname === "/") return new Response("push worker ok", { headers: cors(env) });
    return json(env, 404, { error: "not found" });
  },
};
