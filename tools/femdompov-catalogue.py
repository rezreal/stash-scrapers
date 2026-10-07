#!/usr/bin/env python3
"""Build a CSV catalogue of femdom-pov.me clips matching a filter.

Metadata only — title, performer, tags, description, image, date, URL. The site's
download links are not touched.

    ./femdompov-catalogue.py --tag hypno -o hypno.csv
    ./femdompov-catalogue.py --category femdom-audio --pages 5 -o audio.csv
    ./femdompov-catalogue.py --url https://femdom-pov.me/latest-news/ --fast -o recent.csv

Deliberately a separate program from joime-catalogue.py rather than a shared one: the two
sites run different software (WordPress here, DataLife Engine there) and agree on almost
nothing but the need for HTTP/2.
"""

from __future__ import annotations

import argparse
import csv
import html
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse

# The site serves only over HTTP/2 — forced to HTTP/1.1 it answers 403 to everything,
# whatever the headers say. Python has no HTTP/2 client in the standard library, so this
# goes through curl. Verified: curl --http2 -> 200, --http1.1 -> 403.
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0.0.0 Safari/537.36")
BASE = "https://femdom-pov.me"
CURL = shutil.which("curl")
THROTTLE = threading.Lock()
DELAY = 0.3
# Cloudflare fronts the site and rate-limits bursts, so retry rather than hammer.
BACKOFF = (5, 15, 30)
FIELDS = ["url", "slug", "source", "performer", "title", "tags", "description",
          "image", "posted"]


class FetchError(Exception):
    pass


class NotFound(FetchError):
    """A 404 — for a listing page that means we have run off the end."""


def fetch(url: str) -> str:
    if not CURL:
        raise FetchError("curl is required (the site is HTTP/2 only) but was not found")
    for pause in (*BACKOFF, None):
        proc = subprocess.run(
            [CURL, "-sS", "--http2", "--compressed", "--location", "--max-time", "60",
             "-A", UA, "-w", "\n%{http_code}", url],
            capture_output=True)
        if proc.returncode != 0:
            raise FetchError(proc.stderr.decode("utf-8", "replace").strip()
                             or f"curl exit {proc.returncode}")
        body, _, code = proc.stdout.decode("utf-8", "replace").rpartition("\n")
        if code == "200":
            return body
        if code == "404":
            raise NotFound("HTTP 404")
        if code == "429" and pause is not None:
            with THROTTLE:
                time.sleep(pause)
            continue
        raise FetchError(f"HTTP {code}")
    raise FetchError("HTTP 429 after retries")


def text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def meta(page: str, key: str, attr: str = "property") -> str:
    m = re.search(r'<meta[^>]*%s="%s"[^>]*content="([^"]*)"' % (attr, re.escape(key)), page)
    if not m:
        m = re.search(r'<meta[^>]*content="([^"]*)"[^>]*%s="%s"' % (attr, re.escape(key)), page)
    return html.unescape(m.group(1)).strip() if m else ""


def split_title(full: str) -> tuple[str, str]:
    """"Performer - Title | Site" -> (performer, title).

    The site suffix varies (37 of 40 sampled posts end "| Femdom POV", 3 with the longer
    site title), so it comes off at the last "|" rather than by matching a literal.
    """
    core = full.rsplit("|", 1)[0].strip() if "|" in full else full.strip()
    performer, sep, title = core.partition(" - ")
    return (performer.strip(), title.strip()) if sep else ("", core)


def listing_url(args: argparse.Namespace, page: int) -> str:
    if args.tag:
        base = f"{BASE}/tag/{urllib.parse.quote(args.tag.strip().lower().replace(' ', '-'))}/"
    elif args.category:
        base = f"{BASE}/category/{urllib.parse.quote(args.category.strip().lower())}/"
    else:
        base = args.url if args.url.endswith("/") else args.url + "/"
    return base if page == 1 else f"{base}page/{page}/"


def parse_listing(page: str, source: str) -> list[dict]:
    """One row per main_box_inner card: URL, title and cover. Tags and the description
    only exist on the post page."""
    rows = []
    for chunk in re.split(r'<div class="main_box_inner"', page)[1:]:
        link = re.search(r'<div class="main_box_title">\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                         chunk, re.S)
        if not link:
            continue
        url = html.unescape(link.group(1))
        row = {f: "" for f in FIELDS}
        row["url"] = url
        row["slug"] = url.rstrip("/").rsplit("/", 1)[-1]
        row["source"] = source
        row["performer"], row["title"] = split_title(text(link.group(2)))
        img = re.search(r'<img[^>]*src="([^"]+)"', chunk)
        if img:
            src = html.unescape(img.group(1))
            row["image"] = src if src.startswith("http") else BASE + src
        rows.append(row)
    return rows


def add_details(row: dict) -> None:
    """og:* plus the post's own rel="tag" links.

    rel="tag" matters: the page also carries a sidebar cloud of ~45 tag links that is
    identical on every post, so matching /tag/ hrefs would tag everything with the cloud.
    """
    try:
        page = fetch(row["url"])
    except FetchError as exc:
        print(f"    ! {row['url']}: {exc}", file=sys.stderr, flush=True)
        return
    title = meta(page, "og:title")
    if title:
        performer, clean_title = split_title(title)
        row["performer"] = performer or row["performer"]
        row["title"] = clean_title or row["title"]
    row["description"] = meta(page, "og:description")
    row["posted"] = meta(page, "article:published_time")[:10]
    row["image"] = meta(page, "og:image") or row["image"]
    tags = [text(t) for t in re.findall(r'<a[^>]*rel="tag"[^>]*>(.*?)</a>', page, re.S)]
    row["tags"] = " | ".join(dict.fromkeys(t for t in tags if t))
    time.sleep(DELAY)


class Writer:
    """Writes rows as they are gathered, so an interrupted run still leaves a usable CSV."""

    def __init__(self, path: str, append: bool):
        exists = append and os.path.exists(path)
        self.fh = open(path, "a" if exists else "w", newline="", encoding="utf-8")
        self.writer = csv.DictWriter(self.fh, fieldnames=FIELDS)
        if not exists:
            self.writer.writeheader()
            self.fh.flush()
        self.count = 0

    def write(self, row: dict) -> None:
        self.writer.writerow(row)
        self.fh.flush()
        self.count += 1

    def close(self) -> None:
        self.fh.close()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--tag", help="a site tag, e.g. hypno or 'foot worship'")
    src.add_argument("--category", help="a site category, e.g. femdom-audio")
    src.add_argument("--url", help="any listing URL")
    p.add_argument("-o", "--out", default="femdompov.csv", help="CSV path")
    p.add_argument("--pages", type=int, default=0, help="stop after N listing pages")
    p.add_argument("--limit", type=int, default=0, help="stop after N clips")
    p.add_argument("--fast", action="store_true",
                   help="listing pages only — no tags, description or date")
    p.add_argument("--append", action="store_true",
                   help="continue an existing CSV, skipping URLs already in it")
    args = p.parse_args()
    # NB: the site's search is disabled — /?s=term 302s to the homepage — so there is no
    # --search here, unlike joime-catalogue.py.

    known: set[str] = set()
    if args.append and os.path.exists(args.out):
        with open(args.out, newline="", encoding="utf-8") as fh:
            known = {r["url"] for r in csv.DictReader(fh) if r.get("url")}
        print(f"{len(known)} clips already in {args.out}", flush=True)

    source = args.tag or args.category or args.url
    writer = Writer(args.out, args.append)
    print(f"writing {args.out} as it goes — safe to stop with Ctrl-C", flush=True)

    page = 1
    try:
        while True:
            try:
                found = parse_listing(fetch(listing_url(args, page)), source)
            except NotFound:
                # Past the last page WordPress answers 404 rather than an empty listing.
                print(f"  page {page:4d}: 404, end of listing", flush=True)
                break
            except FetchError as exc:
                print(f"  page {page} failed: {exc}", file=sys.stderr, flush=True)
                break
            if not found:
                break
            fresh = [r for r in found if r["url"] not in known]
            for r in fresh:
                known.add(r["url"])
            if args.limit:
                fresh = fresh[: max(0, args.limit - writer.count)]
            if not args.fast:
                for r in fresh:
                    add_details(r)
            for r in fresh:
                writer.write(r)
            print(f"  page {page:4d}: {len(found):3d} clips, {len(fresh):3d} new"
                  f"  (written {writer.count})", flush=True)
            if args.limit and writer.count >= args.limit:
                break
            if args.pages and page >= args.pages:
                break
            page += 1
            time.sleep(DELAY)
    except KeyboardInterrupt:
        print("\ninterrupted — keeping what was written so far", flush=True)

    writer.close()
    print(f"wrote {writer.count} clips -> {args.out}", flush=True)
    return 0 if writer.count else 1


if __name__ == "__main__":
    sys.exit(main())
