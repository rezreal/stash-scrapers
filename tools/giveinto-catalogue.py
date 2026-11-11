#!/usr/bin/env python3
"""CSV catalogue of giveinto.me (Earesistible) files.

The whole catalogue comes back in a single API response — 183 items at the time of
writing — so this makes exactly one request, with no crawling and no pagination.

    ./giveinto-catalogue.py -o giveinto.csv
    ./giveinto-catalogue.py --tier Free --type 'Hypno: Full' -o free-hypno.csv
"""

from __future__ import annotations

import argparse
import csv
import html.parser
import json
import re
import sys
import urllib.error
import urllib.request

UA = "Mozilla/5.0 (X11; Linux x86_64) giveinto-catalogue/1.0"
API = "https://giveinto.me/api"
SITE = "https://giveinto.me"
# Patreon tier ids, from the site's own bundle. -1 means free.
TIERS = {-1: "Free", 7841239: "Good Pet", 7841240: "Loving Pet",
         7841241: "Submissive Pet", 7841330: "Dedicated Pet",
         7841449: "Devoted Hypnoslut"}
FIELDS = ["url", "file_id", "variant_id", "title", "type", "tier", "audiences", "tags",
          "variants", "duration", "duration_s", "released", "image", "synopsis"]


class Stripper(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []

    def handle_data(self, data):
        self.out.append(data)

    @property
    def text(self) -> str:
        return re.sub(r"\s+", " ", "".join(self.out)).strip()


def to_text(markup: str) -> str:
    if not markup:
        return ""
    s = Stripper()
    s.feed(markup)
    return s.text


def seconds(duration: str) -> int | None:
    total, found = 0, False
    for value, unit in re.findall(r"(\d+)\s*([hms])", duration or ""):
        total += int(value) * {"h": 3600, "m": 60, "s": 1}[unit]
        found = True
    return total if found else None


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-o", "--out", default="giveinto.csv", help="CSV path")
    p.add_argument("--tier", help="filter by tier name, e.g. Free")
    p.add_argument("--type", dest="ftype", help="filter by type, e.g. 'Hypno: Full'")
    p.add_argument("--audience", help="filter by audience, e.g. F4M")
    p.add_argument("--tag", help="filter by tag (substring, case-insensitive)")
    p.add_argument("--since", metavar="YYYY-MM-DD", help="filter by release date")
    p.add_argument("--list", action="store_true", help="print instead of writing a CSV")
    args = p.parse_args()

    req = urllib.request.Request(f"{API}/list/full?getFilters=1",
                                 headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            items = json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"fetch failed: {exc}", file=sys.stderr)
        return 1
    if not isinstance(items, list):
        print("unexpected response shape", file=sys.stderr)
        return 1
    print(f"{len(items)} files in the catalogue (one request)")

    rows = []
    for it in items:
        meta = it.get("meta") or {}
        tier = meta.get("min_tier")
        row = {
            "url": f"{SITE}/file/{it.get('first_variant_id','')}",
            "file_id": it.get("_id", ""),
            "variant_id": it.get("first_variant_id", ""),
            "title": it.get("title", ""),
            "type": it.get("type", ""),
            "tier": TIERS.get(tier, tier if tier is not None else ""),
            "audiences": " | ".join(meta.get("audiences") or []),
            "tags": " | ".join(meta.get("tags") or []),
            "variants": meta.get("variants", ""),
            "duration": it.get("first_variant_duration", ""),
            "duration_s": seconds(it.get("first_variant_duration", "")) or "",
            "released": (meta.get("latest_release") or "")[:10],
            "image": it.get("image", ""),
            "synopsis": to_text(it.get("synopsis_html") or ""),
        }
        rows.append(row)

    if args.tier:
        rows = [r for r in rows if str(r["tier"]).lower() == args.tier.lower()]
    if args.ftype:
        rows = [r for r in rows if r["type"].lower() == args.ftype.lower()]
    if args.audience:
        rows = [r for r in rows if args.audience.lower() in r["audiences"].lower()]
    if args.tag:
        rows = [r for r in rows if args.tag.lower() in r["tags"].lower()]
    if args.since:
        rows = [r for r in rows if r["released"] >= args.since]
    rows.sort(key=lambda r: (r["released"], r["title"]), reverse=True)

    print(f"{len(rows)} match the filters")
    if args.list:
        for r in rows:
            print(f"  {r['released']}  {r['tier']:<16} {r['duration']:>10}  {r['title']}")
        return 0 if rows else 1

    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} files -> {args.out}")
    return 0 if rows else 1


if __name__ == "__main__":
    sys.exit(main())
