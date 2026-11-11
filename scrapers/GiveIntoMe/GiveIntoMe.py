#!/usr/bin/env python3
"""Stash scene scraper for giveinto.me (Earesistible).

Reads {"url": ...} or {"name": ...} on stdin and writes a scene object on stdout, per
Stash's scrapeScript protocol. Standard library only.

The site is a Next.js static export: every page ships as a ~2.6 KB shell with empty
pageProps, no title, no og tags and no sitemap, so there is nothing in the HTML to scrape.
All data comes from its JSON API, which is therefore what this uses:

    GET /api/get/<variant_id>        one file, with every variant
    GET /api/list/full?getFilters=1  the whole catalogue — 183 items in a single response

Note that /api/ is Disallowed in the site's robots.txt. Requests here are per-scrape and
rate-limited, and the catalogue is cached rather than re-fetched per file.
"""

from __future__ import annotations

import html
import html.parser
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (X11; Linux x86_64) giveinto-scraper/1.0"
API = "https://giveinto.me/api"
SITE = "https://giveinto.me"
CREATOR = "Earesistible"
# Patreon tier ids, lifted from the site's own bundle. -1 means free.
TIERS = {
    -1: "Free",
    7841239: "Good Pet",
    7841240: "Loving Pet",
    7841241: "Submissive Pet",
    7841330: "Dedicated Pet",
    7841449: "Devoted Hypnoslut",
}
# Gated fields come back as this sentinel rather than a URL.
UPGRADE = "[UPGRADE]"
CACHE_TTL = 6 * 3600
DELAY = 0.4


class Stripper(html.parser.HTMLParser):
    """Minimal html -> text, keeping paragraph breaks."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in ("p", "br", "div", "li"):
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in ("p", "div", "li"):
            self.out.append("\n")

    def handle_data(self, data):
        self.out.append(data)

    @property
    def text(self) -> str:
        t = re.sub(r"[^\S\n]+", " ", "".join(self.out))
        t = re.sub(r" *\n *", "\n", t)
        return re.sub(r"\n{3,}", "\n\n", t).strip()


def to_text(markup: str) -> str:
    if not markup:
        return ""
    s = Stripper()
    s.feed(markup)
    return s.text


def get(path: str) -> dict | list:
    req = urllib.request.Request(API + path, headers={
        "User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def cache_path() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    name = "giveinto-catalogue.json"
    return os.path.join(here if os.access(here, os.W_OK) else tempfile.gettempdir(), name)


def catalogue(log=lambda m: None) -> list:
    """The whole catalogue, cached. One request serves every lookup."""
    path = cache_path()
    try:
        blob = json.load(open(path, encoding="utf-8"))
        if time.time() - blob.get("fetched", 0) < CACHE_TTL:
            return blob.get("items", [])
    except (OSError, ValueError):
        pass
    try:
        items = get("/list/full?getFilters=1")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        log(f"catalogue unavailable: {exc}")
        return []
    if not isinstance(items, list):
        return []
    try:
        json.dump({"fetched": time.time(), "items": items}, open(path, "w", encoding="utf-8"))
    except OSError as exc:
        log(f"could not write {path}: {exc}")
    return items


def seconds(duration: str) -> int | None:
    """'7m 27s' / '1h 2m 3s' / '45s' -> seconds. Double spaces occur in the data."""
    if not duration:
        return None
    total = 0
    found = False
    for value, unit in re.findall(r"(\d+)\s*([hms])", duration):
        total += int(value) * {"h": 3600, "m": 60, "s": 1}[unit]
        found = True
    return total if found else None


def scrape_file(data: dict, variant_id: str | None, url: str) -> dict:
    variants = data.get("variants") or []
    variant = next((v for v in variants if v.get("_id") == variant_id), None) or (
        variants[0] if variants else {})

    scene: dict = {"url": url, "studio": {"name": CREATOR},
                   "performers": [{"name": CREATOR}]}
    title = (data.get("title") or "").strip()
    # A variant may carry its own title ("Naked", "No Binaurals", ...); keep both.
    vtitle = (variant.get("title") or "").strip()
    scene["title"] = f"{title} ({vtitle})" if vtitle else title
    if data.get("_id"):
        scene["code"] = data["_id"]
    if data.get("image"):
        scene["image"] = data["image"]

    date = (variant.get("date") or "")[:10]
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        scene["date"] = date
    secs = seconds(variant.get("duration") or "")
    if secs:
        scene["duration"] = secs

    # Details: the synopsis, then the longer description, then the labelled blocks the
    # site shows beneath it. Stash renders details as plain text, so these are prefixed
    # rather than marked up.
    blocks = [to_text(data.get("synopsis_html") or ""),
              to_text(data.get("description_html") or ""),
              to_text(variant.get("description_html") or "")]
    for label in ("instructions", "intended_effect", "notes"):
        value = (data.get(label) or "").strip()
        if value:
            blocks.append(f"{label.replace('_', ' ').title()}: {to_text(value)}")
    details = "\n\n".join(b for b in blocks if b)
    if details:
        scene["details"] = details

    meta = data.get("meta") or {}
    tags: list[str] = []
    if data.get("type"):
        tags.append(f"Type: {data['type']}")
    if variant.get("audience"):
        tags.append(variant["audience"])
    # Variant tags are a comma-joined string here, unlike the list in the catalogue.
    raw_tags = variant.get("tags")
    if isinstance(raw_tags, str):
        tags += [t.strip() for t in raw_tags.split(",") if t.strip()]
    elif isinstance(raw_tags, list):
        tags += [str(t).strip() for t in raw_tags if str(t).strip()]
    else:
        tags += [str(t).strip() for t in (meta.get("tags") or []) if str(t).strip()]
    for value in (data.get("tone") or "").split(","):
        if value.strip():
            tags.append(f"Tone: {value.strip()}")
    tier = variant.get("tier_listen", meta.get("min_tier"))
    if isinstance(tier, int):
        tags.append(f"Tier: {TIERS.get(tier, tier)}")
    if (variant.get("orgasm") or "0") not in ("0", "", None):
        tags.append("Orgasm")

    seen, unique = set(), []
    for t in tags:
        if t and t not in seen:
            seen.add(t)
            unique.append(t)
    if unique:
        scene["tags"] = [{"name": t} for t in unique]
    return scene


def by_name(name: str) -> list[dict]:
    """Search the cached catalogue. Closest title first."""
    import difflib

    query = re.sub(r"\.(opus|mp3|m4a|flac|wav|funscript)$", "", name.strip(), flags=re.I)
    query = re.sub(r"^\s*\[[^\]]{1,8}\]\s*", "", query)        # drop an [F4M] prefix
    norm = lambda s: re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).strip()
    target = norm(query)

    results = []
    for item in catalogue(log=lambda m: print(m, file=sys.stderr)):
        title = item.get("title") or ""
        if not title:
            continue
        ratio = difflib.SequenceMatcher(None, norm(title), target).ratio()
        if target in norm(title) or norm(title) in target or ratio > 0.55:
            entry = {"title": title,
                     "url": f"{SITE}/file/{item.get('first_variant_id')}"}
            if item.get("image"):
                entry["image"] = item["image"]
            syn = to_text(item.get("synopsis_html") or "")
            if syn:
                entry["details"] = syn
            date = ((item.get("meta") or {}).get("latest_release") or "")[:10]
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
                entry["date"] = date
            results.append((ratio, entry))
    results.sort(key=lambda r: r[0], reverse=True)
    return [e for _, e in results[:25]]


def main() -> int:
    action = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else ""
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        payload = {}

    if action == "sceneByName":
        name = payload.get("name") or payload.get("title") or ""
        if not name:
            print("[]")
            return 1
        print(json.dumps(by_name(name)))
        return 0

    url = payload.get("url") or (payload.get("urls") or [None])[0]
    if not url:
        print("no url supplied", file=sys.stderr)
        print("{}")
        return 1
    m = re.search(r"/file/([A-Za-z0-9-]+)", url)
    if not m:
        print(f"not a giveinto.me file url: {url}", file=sys.stderr)
        print("{}")
        return 1
    variant_id = m.group(1)
    try:
        data = get(f"/get/{urllib.parse.quote(variant_id)}")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"fetch failed: {exc}", file=sys.stderr)
        print("{}")
        return 1
    if not isinstance(data, dict) or not data.get("title"):
        print("{}")
        return 1
    print(json.dumps(scrape_file(data, variant_id, url)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
