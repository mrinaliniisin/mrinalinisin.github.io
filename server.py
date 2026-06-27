#!/usr/bin/env python3
"""Local save server for the blog.

Serves the static site AND backs editor.html with two endpoints:

  POST /api/save   {title, date, markdown, html}  -> writes blog/<slug>.html
                                                      and updates blog/index.html
  GET  /api/load?p=<slug>                          -> {title, date, markdown}

Posts are pre-rendered: editor.html renders markdown -> HTML with marked.js at
save time and POSTs both; this server just writes files. The markdown source is
preserved inside each post as an <!--EDIT:post:b64:...--> comment, so the editor
can reload and re-save a post. Publishing is a normal `git push` afterwards.

Run from the repo root:  python3 server.py [port]   (default 5666)

5666 is the port the always-on runit service uses, so the default matches it.
"""

import base64
import html
import json
import os
import re
import sys
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.getcwd()
BLOG = os.path.join(ROOT, "blog")
INDEX = os.path.join(BLOG, "index.html")
IMAGES = os.path.join(BLOG, "images")

# Clipboard images arrive as a MIME type, not a filename, so map it to a suffix.
IMAGE_EXT = {
    "image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg",
    "image/gif": "gif", "image/webp": "webp", "image/svg+xml": "svg",
}

POST_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title_attr} · Mrinalini S</title>
  <meta name="post-title" content="{title_attr}">
  <meta name="post-date" content="{date_iso}">
  <link rel="stylesheet" href="/assets/blog.css">
</head>
<body>
  <main class="wrap">
    <a class="back" href="/blog/">← All posts</a>
    <article>
      <h1 class="post-title">{title_html}</h1>
      <p class="post-meta">{date_human}</p>
<!--EDIT:post:b64:{b64}-->
      <div class="post-body" data-edit-id="post" data-edit-file="blog/{slug}.html">
{body_html}
      </div>
<!--/EDIT:post-->
    </article>
  </main>
  <footer>&copy; 2026 Mrinalini S · Code licensed under MIT</footer>
</body>
</html>
"""


def slugify(text):
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "post"


def human_date(iso):
    try:
        d = datetime.strptime(iso, "%Y-%m-%d")
        return d.strftime("%B ") + str(d.day) + d.strftime(", %Y")
    except ValueError:
        return iso


def write_post(title, date_iso, markdown, body_html):
    slug = slugify(title)
    # The title comes from the title field and is rendered by the template, so
    # drop a leading <h1> from the body to avoid showing the title twice.
    body_html = re.sub(r"^\s*<h1\b[^>]*>.*?</h1>\s*", "", body_html,
                       count=1, flags=re.S | re.I)
    b64 = base64.b64encode(markdown.encode("utf-8")).decode("ascii")
    page = POST_TEMPLATE.format(
        title_attr=html.escape(title, quote=True),
        title_html=html.escape(title),
        date_iso=date_iso,
        date_human=human_date(date_iso),
        b64=b64,
        slug=slug,
        body_html=body_html,
    )
    with open(os.path.join(BLOG, slug + ".html"), "w", encoding="utf-8") as f:
        f.write(page)
    update_index(slug, title, date_iso)
    return slug


def update_index(slug, title, date_iso):
    with open(INDEX, encoding="utf-8") as f:
        lines = f.read().splitlines()

    href = '/blog/%s.html' % slug
    # Drop any existing entry for this slug and the "no posts" placeholder.
    lines = [ln for ln in lines
             if href not in ln and "empty-placeholder" not in ln]

    li = ('      <li><a href="%s"><span class="t">%s</span>'
          '<span class="post-meta">%s</span></a></li>'
          % (href, html.escape(title), human_date(date_iso)))

    out = []
    for ln in lines:
        out.append(ln)
        if "<!--POSTS-->" in ln:
            out.append(li)  # newest first, right under the marker
    with open(INDEX, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")


def save_image(title, mime, b64):
    ext = IMAGE_EXT.get((mime or "").lower())
    if not ext:
        raise ValueError("unsupported image type: %r" % mime)
    os.makedirs(IMAGES, exist_ok=True)
    # Name images after the post slug so they sort and read sensibly; before a
    # title exists, fall back to a timestamp so the paste still works.
    base = slugify(title) if title.strip() else datetime.now().strftime("img-%Y%m%d-%H%M%S")
    n = 1
    while os.path.exists(os.path.join(IMAGES, "%s-%d.%s" % (base, n, ext))):
        n += 1
    name = "%s-%d.%s" % (base, n, ext)
    with open(os.path.join(IMAGES, name), "wb") as f:
        f.write(base64.b64decode(b64))
    return "/blog/images/" + name


def load_post(slug):
    path = os.path.join(BLOG, slug + ".html")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        src = f.read()
    title = re.search(r'name="post-title" content="([^"]*)"', src)
    date = re.search(r'name="post-date" content="([^"]*)"', src)
    b64 = re.search(r"<!--EDIT:post:b64:(.*?)-->", src, re.S)
    markdown = ""
    if b64:
        markdown = base64.b64decode(b64.group(1)).decode("utf-8")
    return {
        "title": html.unescape(title.group(1)) if title else "",
        "date": date.group(1) if date else "",
        "markdown": markdown,
    }


def post_files():
    """Every published post HTML file (skips the listing index)."""
    if not os.path.isdir(BLOG):
        return []
    return [os.path.join(BLOG, n) for n in sorted(os.listdir(BLOG))
            if n.endswith(".html") and n != "index.html"]


def list_images():
    if not os.path.isdir(IMAGES):
        return []
    names = sorted(n for n in os.listdir(IMAGES)
                   if os.path.isfile(os.path.join(IMAGES, n)) and not n.startswith("."))
    # Map each image to the post slugs that reference it, by scanning the
    # rendered HTML (the human-readable /blog/images/<name> in each post body).
    usage = {n: [] for n in names}
    for path in post_files():
        with open(path, encoding="utf-8") as f:
            src = f.read()
        slug = os.path.basename(path)[:-5]
        for n in names:
            if "/blog/images/" + n in src:
                usage[n].append(slug)
    out = []
    for n in names:
        st = os.stat(os.path.join(IMAGES, n))
        out.append({"name": n, "size": st.st_size,
                    "url": "/blog/images/" + n, "used_in": usage[n]})
    return out


def update_image_refs(old_name, new_name):
    """Repoint every reference to old_name at new_name, in both the rendered
    body HTML and the base64 Markdown source preserved in each post. Returns
    the number of posts changed."""
    old_ref = "/blog/images/" + old_name
    new_ref = "/blog/images/" + new_name
    count = 0
    for path in post_files():
        with open(path, encoding="utf-8") as f:
            src = f.read()
        changed = [old_ref in src]  # list so the nested repl can flip it
        src = src.replace(old_ref, new_ref)  # rendered <img src> occurrences

        def repl(m):  # the base64-encoded Markdown isn't touched by the replace above
            md = base64.b64decode(m.group(1)).decode("utf-8")
            if old_ref not in md:
                return m.group(0)
            changed[0] = True
            md = md.replace(old_ref, new_ref)
            return "<!--EDIT:post:b64:" + base64.b64encode(
                md.encode("utf-8")).decode("ascii") + "-->"

        src = re.sub(r"<!--EDIT:post:b64:(.*?)-->", repl, src, flags=re.S)
        if changed[0]:
            with open(path, "w", encoding="utf-8") as f:
                f.write(src)
            count += 1
    return count


def rename_image(old, new):
    old = os.path.basename(old or "")
    src_path = os.path.join(IMAGES, old)
    if not old or not os.path.isfile(src_path):
        raise ValueError("no such image: %r" % old)
    ext = os.path.splitext(old)[1].lower()             # keep the original format
    stem = slugify(os.path.splitext(os.path.basename(new or ""))[0])
    if not stem:
        raise ValueError("invalid new name")
    new_name = stem + ext
    if new_name == old:
        return {"name": old, "updated": 0}
    if os.path.exists(os.path.join(IMAGES, new_name)):
        raise ValueError("an image named %s already exists" % new_name)
    os.rename(src_path, os.path.join(IMAGES, new_name))
    return {"name": new_name, "updated": update_image_refs(old, new_name)}


def delete_image(name):
    name = os.path.basename(name or "")
    path = os.path.join(IMAGES, name)
    if not name or not os.path.isfile(path):
        raise ValueError("no such image: %r" % name)
    os.remove(path)
    return {"name": name}


class Handler(SimpleHTTPRequestHandler):
    def _json(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self):
        if self.path.startswith("/api/load"):
            slug = slugify(self.path.split("p=", 1)[1]) if "p=" in self.path else ""
            post = load_post(slug) if slug else None
            return self._json(200 if post else 404, post or {"error": "not found"})
        if self.path == "/api/images":
            return self._json(200, {"images": list_images()})
        return super().do_GET()

    def do_POST(self):
        try:
            if self.path == "/api/upload":
                d = self._body()
                url = save_image(d.get("title", ""), d.get("mime", ""), d.get("b64", ""))
                return self._json(200, {"ok": True, "url": url})
            if self.path == "/api/image/rename":
                d = self._body()
                return self._json(200, {"ok": True, **rename_image(d.get("old"), d.get("new"))})
            if self.path == "/api/image/delete":
                d = self._body()
                return self._json(200, {"ok": True, **delete_image(d.get("name"))})
            if self.path == "/api/save":
                d = self._body()
                title = (d.get("title") or "").strip()
                if not title:
                    return self._json(400, {"error": "title is required"})
                date_iso = (d.get("date") or datetime.now().strftime("%Y-%m-%d")).strip()
                slug = write_post(title, date_iso, d.get("markdown", ""), d.get("html", ""))
                return self._json(200, {"ok": True, "slug": slug,
                                        "url": "/blog/%s.html" % slug})
            return self._json(404, {"error": "unknown endpoint"})
        except Exception as e:  # surface the error to the editor/gallery UI
            return self._json(500, {"error": str(e)})

    def log_message(self, *a):
        pass  # quiet


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5666
    print("Blog save server on http://localhost:%d" % port)
    print("  editor:  http://localhost:%d/editor.html" % port)
    print("  gallery: http://localhost:%d/gallery.html" % port)
    print("  blog:    http://localhost:%d/blog/" % port)
    ThreadingHTTPServer(("", port), Handler).serve_forever()
