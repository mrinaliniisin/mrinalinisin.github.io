// Subscribe/unsubscribe UI for push notifications on the homepage.
(() => {
  // ▼ After deploying the Worker, set this to its URL (also set in sw.js).
  const WORKER = "https://mrinalinisin-push.mustardseed.workers.dev";
  // VAPID public key (safe to expose). Must match the Worker's VAPID_PUBLIC.
  const VAPID_PUBLIC = "BBl7CjwTobyvrKM1fgcfhH5YujNqIR_5dA6EwNRI7LnFJyAmP9_ja2wdy0fSgFTPWSl2MX1K7yxqfmTfaPT2-XY";

  const btn = document.getElementById("notify");
  if (!btn) return;

  // Push needs a secure context + service workers + the Push API.
  if (!("serviceWorker" in navigator) || !("PushManager" in window) || !window.isSecureContext) {
    btn.hidden = true;
    return;
  }

  const urlB64ToBytes = b64 => {
    const pad = "=".repeat((4 - (b64.length % 4)) % 4);
    const s = (b64 + pad).replace(/-/g, "+").replace(/_/g, "/");
    return Uint8Array.from(atob(s), c => c.charCodeAt(0));
  };

  const setState = on => {
    btn.dataset.on = on ? "1" : "";
    btn.textContent = on ? "🔔 Subscribed — tap to stop" : "🔔 Notify me of new stuff";
  };

  let reg;
  const ready = navigator.serviceWorker.register("/sw.js").then(async r => {
    reg = r;
    setState(!!(await reg.pushManager.getSubscription()));
    btn.disabled = false;
  }).catch(() => { btn.hidden = true; });

  btn.addEventListener("click", async () => {
    await ready;
    btn.disabled = true;
    try {
      const existing = await reg.pushManager.getSubscription();
      if (existing) {
        await fetch(WORKER + "/unsubscribe", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ endpoint: existing.endpoint }),
        }).catch(() => {});
        await existing.unsubscribe();
        setState(false);
        return;
      }
      if ((await Notification.requestPermission()) !== "granted") { setState(false); return; }
      const sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlB64ToBytes(VAPID_PUBLIC),
      });
      const res = await fetch(WORKER + "/subscribe", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(sub),
      });
      setState(res.ok);
      if (!res.ok) await sub.unsubscribe().catch(() => {});
    } catch (e) {
      setState(false);
    } finally {
      btn.disabled = false;
    }
  });
})();
