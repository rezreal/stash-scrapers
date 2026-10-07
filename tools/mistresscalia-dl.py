#!/usr/bin/env python3
"""Download the audio (and matching funscript / cover) for a free mistresscalia.com post.

Stash's scraper interface can only return metadata, never files, so this lives
outside the scraper.  It saves the audio and the funscript under one shared
basename, which is what Stash keys on when it flags a scene as interactive:

    <dir>/<slug>.mp3
    <dir>/<slug>.funscript
    <dir>/<slug>.jpg        (with --cover)

Paid posts only expose a short sample behind /audioprotectsample/, so they are
skipped rather than half-downloaded.

    ./mistresscalia-dl.py https://mistresscalia.com/rival-cheerleader-hypnotizes-you/ -o ~/stash/audio
"""

from __future__ import annotations

import argparse
import html
import os
import re
import sys
import urllib.error
import urllib.request

UA = "Mozilla/5.0 (X11; Linux x86_64) mistresscalia-dl/1.0"
FREE_PREFIX = "https://mistresscalia.com/wp-content/uploads/"


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read()
    return raw.decode("utf-8", "replace")


def attrs(tag: str) -> dict[str, str]:
    return {
        m.group(1).lower(): html.unescape(m.group(2))
        for m in re.finditer(r'([\w:-]+)\s*=\s*"([^"]*)"', tag)
    }


def meta(page: str, prop: str) -> str | None:
    m = re.search(
        r'<meta[^>]+(?:property|name)=["\']%s["\'][^>]*>' % re.escape(prop), page, re.I
    )
    return attrs(m.group(0)).get("content") if m else None


def parse(page: str) -> dict:
    """Pull the download-relevant bits out of a post page."""
    info: dict = {
        "title": meta(page, "og:title"),
        "date": (meta(page, "article:published_time") or "")[:10] or None,
        "url": meta(page, "og:url"),
        "cover": meta(page, "og:image"),
        "audio": None,
        "funscript": None,
    }

    # The "Download Audio" link is only rendered for free files.
    for m in re.finditer(r"<a\b[^>]*>", page):
        a = attrs(m.group(0))
        if "acp-force-download" in a.get("class", "") and a.get("href", "").startswith(FREE_PREFIX):
            info["audio"] = a["href"]
            break

    # Main player: <audio class="... haptic_audio ..." data-funscript="...">
    for m in re.finditer(r"<audio\b[^>]*>", page, re.S):
        a = attrs(m.group(0))
        if "haptic_audio" not in a.get("class", ""):
            continue
        if a.get("data-funscript"):
            info["funscript"] = a["data-funscript"]
        if not info["audio"]:
            src = re.search(r"<source\b[^>]*>", page[m.end() : m.end() + 2000])
            href = attrs(src.group(0)).get("src", "") if src else ""
            if href.startswith(FREE_PREFIX):
                info["audio"] = href
        break

    return info


def slugify(text: str) -> str:
    text = re.sub(r"[^\w\s-]", "", html.unescape(text)).strip().lower()
    return re.sub(r"[-\s]+", "-", text) or "untitled"


def download(url: str, dest: str, force: bool, dry_run: bool) -> bool:
    name = os.path.basename(dest)
    if os.path.exists(dest) and not force:
        print(f"    = {name} (exists, skipped)")
        return True
    if dry_run:
        print(f"    → {name}  <- {url}")
        return True
    tmp = dest + ".part"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=120) as resp, open(tmp, "wb") as fh:
            size = 0
            while chunk := resp.read(262144):
                fh.write(chunk)
                size += len(chunk)
    except (urllib.error.URLError, OSError) as exc:
        if os.path.exists(tmp):
            os.remove(tmp)
        print(f"    ! {name}: {exc}", file=sys.stderr)
        return False
    os.replace(tmp, dest)
    print(f"    ✓ {name} ({size / 1048576:.1f} MiB)")
    return True


def handle(url: str, args: argparse.Namespace) -> bool:
    print(url)
    try:
        page = fetch(url)
    except (urllib.error.URLError, OSError) as exc:
        print(f"    ! fetch failed: {exc}", file=sys.stderr)
        return False

    info = parse(page)
    print(f"    {info['title'] or '?'} — {info['date'] or '?'}")

    if not info["audio"]:
        print("    ! no free download on this post (paid / sample only) — skipped")
        return False

    base = slugify(info["title"] or "") if args.name == "title" else url.rstrip("/").rsplit("/", 1)[-1]
    os.makedirs(args.outdir, exist_ok=True)

    ok = download(info["audio"], os.path.join(args.outdir, base + ext_of(info["audio"], ".mp3")),
                  args.force, args.dry_run)
    if info["funscript"] and not args.no_funscript:
        ok &= download(info["funscript"], os.path.join(args.outdir, base + ".funscript"),
                       args.force, args.dry_run)
    elif not args.no_funscript:
        print("    - no funscript on this post")
    if args.cover and info["cover"]:
        ok &= download(info["cover"], os.path.join(args.outdir, base + ext_of(info["cover"], ".jpg")),
                       args.force, args.dry_run)
    return ok


def ext_of(url: str, fallback: str) -> str:
    ext = os.path.splitext(url.split("?")[0])[1]
    return ext if 1 < len(ext) <= 5 else fallback


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("urls", metavar="URL", nargs="+", help="mistresscalia.com post URL(s)")
    p.add_argument("-o", "--outdir", default=".", help="target directory (default: .)")
    p.add_argument("--name", choices=("slug", "title"), default="slug",
                   help="basename source: URL slug (default) or post title")
    p.add_argument("--cover", action="store_true", help="also download the cover image")
    p.add_argument("--no-funscript", action="store_true", help="skip the funscript")
    p.add_argument("-f", "--force", action="store_true", help="re-download existing files")
    p.add_argument("-n", "--dry-run", action="store_true", help="show what would be downloaded")
    args = p.parse_args()

    failed = sum(0 if handle(u, args) else 1 for u in args.urls)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
