#!/usr/bin/env python3
"""Build a CSV catalogue of joi-me.com clips matching a filter.

Metadata only — title, performer, tags, description, image, category, URL. The site's
download links are not touched.

    ./joime-catalogue.py --tag stroking -o stroking.csv
    ./joime-catalogue.py --search "verbal humiliation" --pages 5 -o humiliation.csv
    ./joime-catalogue.py --url https://joi-me.com/lastnews/ --fast -o recent.csv

Listing pages carry everything except the description, so by default each clip's own page
is fetched to add it — one request per clip. --fast skips that and works from the listings
alone, which is roughly eighteen times fewer requests.
"""

from __future__ import annotations

import argparse
import csv
import html
import os
import re
import sys
import itertools
import shutil
import subprocess
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

# The site serves only over HTTP/2 — forced to HTTP/1.1 it answers 403 to every request,
# whatever the headers say. Python has no HTTP/2 client in the standard library, so this
# goes through curl rather than urllib. Verified: curl --http2 -> 200, --http1.1 -> 403.
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0.0.0 Safari/537.36")
CURL = shutil.which("curl")
THROTTLE = threading.Lock()
BASE = "https://joi-me.com"
ARTICLE = re.compile(r"https://joi-me\.com/[a-z0-9-]+/(\d+)-[a-z0-9-]+\.html")
DELAY = 0.3
FIELDS = ["url", "post_id", "category", "performer", "title", "tags", "description",
          "image", "posted"]


class FetchError(Exception):
    pass


# Cloudflare in front of the site rate-limits bursts: six parallel requests earned 429 on
# everything, and the penalty then applied to serial requests for about twenty seconds.
# Backing off and retrying is what actually gets the data.
BACKOFF = (5, 15, 30)


def fetch(url: str) -> str:
    if not CURL:
        raise FetchError("curl is required (the site is HTTP/2 only) but was not found")
    for attempt, pause in enumerate((*BACKOFF, None)):
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
        if code == "429" and pause is not None:
            with THROTTLE:              # one back-off at a time, not one per worker
                time.sleep(pause)
            continue
        raise FetchError(f"HTTP {code}")
    raise FetchError("HTTP 429 after retries")


def text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def listing_url(args: argparse.Namespace, page: int) -> str:
    if args.search:
        q = urllib.parse.quote(args.search)
        return (f"{BASE}/index.php?do=search&subaction=search&story={q}"
                f"&search_start={page}")
    if args.tag:
        # Tag URLs keep spaces as "+", which quote() would turn into %20.
        slug = urllib.parse.quote(args.tag).replace("%20", "+")
        base = f"{BASE}/tags/{slug}/"
    else:
        base = args.url if args.url.endswith("/") else args.url + "/"
    return base if page == 1 else f"{base}page/{page}/"


def parse_listing(page: str) -> list[dict]:
    """One row per <article> card. Cards carry everything but the description."""
    rows = []
    for chunk in re.split(r'<article class="block story', page)[1:]:
        m = ARTICLE.search(chunk)
        if not m:
            continue
        row = {f: "" for f in FIELDS}
        row["url"], row["post_id"] = m.group(0), m.group(1)

        title = re.search(r'<h2 class="title">.*?<a[^>]*>(.*?)</a>', chunk, re.S)
        if title:
            row["title"] = text(title.group(1))
        cat = re.search(r'<div class="category[^"]*">.*?<a[^>]*>(.*?)</a>', chunk, re.S)
        if cat:
            row["category"] = text(cat.group(1))
        img = re.search(r'<img[^>]*src="([^"]+)"', chunk)
        if img:
            src = html.unescape(img.group(1))
            row["image"] = src if src.startswith("http") else BASE + src
        when = re.search(r'<time[^>]*datetime="([^"]+)"', chunk)
        if when:
            row["posted"] = when.group(1)

        # The first /tags/ link is the performer; the rest are tags. Same rule the
        # JoiMe scraper uses, and cross-checkable against the title prefix.
        names = [text(t) for t in re.findall(r'<a[^>]*href="[^"]*/tags/[^"]*"[^>]*>(.*?)</a>',
                                             chunk, re.S)]
        names = [n for n in dict.fromkeys(names) if n]
        if names:
            row["performer"], row["tags"] = names[0], " | ".join(names[1:])
            # A handful of entries are titled with nothing but the performer name
            # (post 84576 is just "EnaFox Marin"), so only strip the prefix when there
            # is something left afterwards.
            if row["title"].lower().startswith(names[0].lower()):
                stripped = row["title"][len(names[0]):].lstrip(" -").strip()
                if stripped:
                    row["title"] = stripped
        rows.append(row)
    return rows


def add_description(row: dict) -> None:
    try:
        page = fetch(row["url"])
    except (FetchError, OSError) as exc:
        print(f"    ! {row['url']}: {exc}", file=sys.stderr)
        return
    m = re.search(r'<meta name="description" content="([^"]*)"', page)
    if m:
        row["description"] = html.unescape(m.group(1)).strip()
    if not row["image"]:
        m = re.search(r'<meta property="og:image" content="([^"]*)"', page)
        if m:
            row["image"] = html.unescape(m.group(1))


class Writer:
    """Writes rows as they are produced, so a long or interrupted run still leaves a
    usable CSV rather than nothing until the very end."""

    def __init__(self, path: str, append: bool):
        exists = append and os.path.exists(path)
        self.fh = open(path, "a" if exists else "w", newline="", encoding="utf-8")
        self.writer = csv.DictWriter(self.fh, fieldnames=FIELDS)
        if not exists:
            self.writer.writeheader()
            self.fh.flush()
        self.lock = threading.Lock()
        self.count = 0

    def write(self, row: dict) -> None:
        with self.lock:
            self.writer.writerow(row)
            self.fh.flush()          # so `wc -l` and tail -f work while it runs
            self.count += 1

    def close(self) -> None:
        self.fh.close()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--tag", help="a site tag, e.g. stroking or 'verbal humiliation'")
    src.add_argument("--search", help="free-text site search")
    src.add_argument("--url", help="any listing URL (a category, /lastnews/, ...)")
    p.add_argument("-o", "--out", default="joime.csv", help="CSV path (default: joime.csv)")
    p.add_argument("--pages", type=int, default=0, help="stop after N listing pages")
    p.add_argument("--limit", type=int, default=0, help="stop after N clips")
    p.add_argument("--fast", action="store_true",
                   help="listing pages only — skips the description, ~18x fewer requests")
    p.add_argument("-j", "--jobs", type=int, default=1,
                   help="parallel requests (default: 1 — the site 429s under concurrency)")
    p.add_argument("--append", action="store_true",
                   help="add to an existing CSV, skipping URLs already in it")
    args = p.parse_args()

    known: set[str] = set()
    if args.append and os.path.exists(args.out):
        with open(args.out, newline="", encoding="utf-8") as fh:
            known = {r["url"] for r in csv.DictReader(fh) if r.get("url")}
        print(f"{len(known)} clips already in {args.out}")

    writer = Writer(args.out, args.append)
    print(f"writing {args.out} as it goes — safe to stop with Ctrl-C", flush=True)

    def get_listing(page: int):
        try:
            return page, parse_listing(fetch(listing_url(args, page)))
        except (FetchError, OSError) as exc:
            print(f"  page {page} failed: {exc}", file=sys.stderr, flush=True)
            return page, None

    total = 0
    stop = False
    try:
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            for start_page in itertools.count(1, args.jobs):
                batch = list(range(start_page, start_page + args.jobs))
                for page, found in sorted(pool.map(get_listing, batch)):
                    if stop or found is None:
                        continue
                    if not found:
                        # Past the last page the site serves a listing with no articles.
                        stop = True
                        continue
                    fresh = [r for r in found if r["url"] not in known]
                    for r in fresh:
                        known.add(r["url"])
                    if args.limit:
                        fresh = fresh[: max(0, args.limit - total)]

                    # Enrich and write this page before moving on, so the CSV is complete
                    # up to whatever point the run is interrupted.
                    if fresh and not args.fast:
                        list(pool.map(add_description, fresh))
                    for r in fresh:
                        writer.write(r)
                    total += len(fresh)

                    print(f"  page {page:5d}: {len(found):3d} clips, {len(fresh):3d} new"
                          f"  (written {total})", flush=True)
                    if args.limit and total >= args.limit:
                        stop = True
                if stop:
                    break
                if args.pages and start_page + args.jobs - 1 >= args.pages:
                    break
                time.sleep(DELAY)
    except KeyboardInterrupt:
        print("\ninterrupted — keeping what was written so far", flush=True)

    writer.close()
    print(f"wrote {writer.count} clips -> {args.out}", flush=True)
    return 0 if writer.count else 1


if __name__ == "__main__":
    sys.exit(main())
