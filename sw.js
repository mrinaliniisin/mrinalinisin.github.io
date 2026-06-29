// Service worker for mrinalinisin.github.io push notifications.
// Pushes arrive payload-less (the broadcaster only signs, doesn't encrypt), so
// on each push we fetch the latest card info from the push Worker's /latest.

// ▼ After deploying the Worker, set this to its URL (also set in push.js).
const WORKER = "https://mrinalinisin-push.mustardseed.workers.dev";

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));

self.addEventListener("push", event => {
  event.waitUntil((async () => {
    let data = { title: "mrinalinisin.github.io", body: "Something new was posted", url: "/" };
    try {
      if (event.data) {
        data = { ...data, ...event.data.json() };          // if a payload is ever sent
      } else {
        const r = await fetch(WORKER + "/latest", { cache: "no-store" });
        if (r.ok) {
          const j = await r.json();
          if (j && j.title) data = { ...data, ...j };
        }
      }
    } catch (_) { /* fall back to the generic message */ }

    await self.registration.showNotification(data.title, {
      body: data.body,
      tag: "mrinalinisin-new",          // collapse duplicates
      data: { url: data.url || "/" },
    });
  })());
});

self.addEventListener("notificationclick", event => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/";
  event.waitUntil((async () => {
    const all = await clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const c of all) {
      if (c.url === url && "focus" in c) return c.focus();
    }
    return clients.openWindow(url);
  })());
});
