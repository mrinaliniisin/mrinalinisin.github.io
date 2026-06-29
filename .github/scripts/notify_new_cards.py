#!/usr/bin/env python3
"""Broadcast a push notification for each card newly added to index.html.

Run by .github/workflows/notify.yml after a push to main. Compares the cards in
the current index.html against the previous commit (HEAD~1); for every card
whose href is new, POSTs {title, body, url} to the push Worker's /broadcast.

Env:
  PUSH_WORKER_URL   e.g. https://mrinalinisin-push.<sub>.workers.dev
  BROADCAST_SECRET  shared bearer token (matches the Worker secret)
  SITE_ORIGIN       optional, default https://mrinalinisin.github.io
"""
import html
import json
import os
import re
import subprocess
import sys
import urllib.request

# href, title, desc — robust to whatever follows inside the card.
CARD_RE = re.compile(
    r'<a class="card-link" href="([^"]+)"[^>]*></a>\s*'
    r'<h2>(.*?)</h2>\s*<div class="desc">(.*?)</div>', re.S)


def cards(src):
    out = {}
    for href, title, desc in CARD_RE.findall(src or ""):
        clean = lambda s: html.unescape(re.sub(r"\s+", " ", s).strip())
        out[href] = (clean(title), clean(desc))
    return out


def prev_index():
    r = subprocess.run(["git", "show", "HEAD~1:index.html"],
                       capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def main():
    worker = os.environ["PUSH_WORKER_URL"].rstrip("/")
    secret = os.environ["BROADCAST_SECRET"]
    origin = os.environ.get("SITE_ORIGIN", "https://mrinalinisin.github.io").rstrip("/")

    new = cards(open("index.html", encoding="utf-8").read())
    old = cards(prev_index())
    added = [h for h in new if h not in old]
    if not added:
        print("No new cards; nothing to notify.")
        return

    for href in added:
        title, desc = new[href]
        url = href if href.startswith("http") else origin + "/" + href.lstrip("/")
        payload = json.dumps({
            "title": "New on mrinalinisin: " + title,
            "body": desc, "url": url,
        }).encode()
        req = urllib.request.Request(
            worker + "/broadcast", data=payload, method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + secret,
                     # Cloudflare's edge 403s the default Python-urllib UA as a bot.
                     "User-Agent": "mrinalinisin-notify/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                print("notified %r -> %s %s" % (title, resp.status, resp.read().decode()))
        except Exception as e:
            print("FAILED to notify %r: %s" % (title, e), file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
