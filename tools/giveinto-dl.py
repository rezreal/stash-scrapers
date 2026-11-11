#!/usr/bin/env python3
"""Download giveinto.me (Earesistible) audio and funscripts.

Stash's scraper interface returns metadata only, so downloads live here. Everything for a
variant is saved under one basename, which is what Stash keys on when pairing a funscript
with its media:

    <dir>/[F4M] Conditioned For Slumber.mp3
    <dir>/[F4M] Conditioned For Slumber.funscript

    ./giveinto-dl.py https://giveinto.me/file/<variant_id> -o ~/stash/earesistible
    ./giveinto-dl.py --from-catalogue --tier Free --limit 10 -o ~/stash/earesistible

Only the primary variant is taken by default; most files have several (104 of 183), and
--all-variants fetches the rest. MP3 is preferred over the default Opus when the account
has access to it.
"""

from __future__ import annotations

import argparse
import html.parser
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (X11; Linux x86_64) giveinto-dl/1.0"
API = "https://giveinto.me/api"
SITE = "https://giveinto.me"
TIERS = {-1: "Free", 7841239: "Good Pet", 7841240: "Loving Pet",
         7841241: "Submissive Pet", 7841330: "Dedicated Pet",
         7841449: "Devoted Hypnoslut"}
# Fields you lack the tier for come back as this sentinel instead of a URL.
UPGRADE = "[UPGRADE]"
DELAY = 0.4
ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def encode(url: str) -> str:
    """The CDN filenames contain spaces and brackets, so the path needs quoting."""
    p = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit(
        (p.scheme, p.netloc, urllib.parse.quote(p.path), p.query, p.fragment))


def api(path: str):
    req = urllib.request.Request(API + path,
                                 headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def sanitize(name: str) -> str:
    name = ILLEGAL.sub("-", name)
    return re.sub(r"\s+", " ", name).strip(" .")[:180].strip(" .")


def remote_stem(url: str) -> str:
    """The CDN filename without its extension — already '[F4M] Some Title'."""
    base = urllib.parse.unquote(urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1])
    return sanitize(os.path.splitext(base)[0])


def download(url: str, dest: str, force: bool, dry_run: bool) -> bool:
    name = os.path.basename(dest)
    if os.path.exists(dest) and not force:
        print(f"    = {name} (exists, skipped)")
        return True
    if dry_run:
        print(f"    → {name}")
        return True
    tmp = dest + ".part"
    try:
        req = urllib.request.Request(encode(url), headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=300) as resp, open(tmp, "wb") as fh:
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
    time.sleep(DELAY)
    return True


def usable(value: str | None) -> str | None:
    """A field holding a real URL, rather than the upgrade sentinel or nothing."""
    if not value or value == UPGRADE or not value.startswith("http"):
        return None
    return value


def handle_variant(data: dict, variant: dict, args: argparse.Namespace) -> bool:
    label = (variant.get("audience") or "?") + (
        f" / {variant['title']}" if variant.get("title") else "")
    tier = variant.get("tier_listen")
    print(f"    [{label}] {variant.get('duration','?')} — tier "
          f"{TIERS.get(tier, tier)}")

    # MP3 first when the account can reach it; audio_url is the default Opus.
    mp3, opus = usable(variant.get("audio_mp3")), usable(variant.get("audio_url"))
    audio = (opus if args.format == "opus" and opus else mp3 or opus)
    if not audio:
        print("    ! no audio available at this tier — skipped")
        return False

    os.makedirs(args.outdir, exist_ok=True)
    stem = remote_stem(audio) if args.name == "remote" else sanitize(
        f"{data.get('title','untitled')}"
        + (f" [{variant['audience']}]" if variant.get("audience") else ""))
    base = os.path.join(args.outdir, stem)
    ext = os.path.splitext(urllib.parse.urlsplit(audio).path)[1] or ".mp3"

    ok = download(audio, base + ext, args.force, args.dry_run)
    if mp3 and opus and audio is mp3:
        pass                       # mp3 chosen; nothing to note
    elif not mp3 and args.format != "opus":
        print("    - mp3 needs a higher tier, took the opus")

    if not args.no_haptics:
        fs = usable(variant.get("haptics_funscript_url"))
        if fs:
            ok &= download(fs, base + ".funscript", args.force, args.dry_run)
        else:
            print("    - no funscript at this tier")

    if args.extras:
        for field, suffix in (("haptics_audio_url", ".haptics"),
                              ("audio_lossless", ".lossless")):
            extra = usable(variant.get(field))
            if extra:
                e = os.path.splitext(urllib.parse.urlsplit(extra).path)[1] or ""
                ok &= download(extra, f"{base}{suffix}{e}", args.force, args.dry_run)
        cover = usable(data.get("image"))
        if cover:
            e = os.path.splitext(urllib.parse.urlsplit(cover).path)[1] or ".webp"
            ok &= download(cover, base + e, args.force, args.dry_run)
    return ok


def handle(variant_id: str, args: argparse.Namespace, seen: set[str]) -> bool:
    if variant_id in seen:
        return True
    seen.add(variant_id)
    try:
        data = api(f"/get/{urllib.parse.quote(variant_id)}")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"  ! {variant_id}: {exc}", file=sys.stderr)
        return False
    time.sleep(DELAY)
    if not isinstance(data, dict) or not data.get("variants"):
        print(f"  ! {variant_id}: nothing returned")
        return False

    variants = data["variants"]
    print(f"  {data.get('title','?')} — {len(variants)} variant(s)")
    chosen = variants if args.all_variants else [
        next((v for v in variants if v.get("_id") == variant_id), variants[0])]
    if len(variants) > 1 and not args.all_variants:
        print(f"    (primary only; --all-variants for the other "
              f"{len(variants) - 1})")

    ok = True
    for v in chosen:
        seen.add(v.get("_id", ""))
        ok &= handle_variant(data, v, args)
    return ok


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("urls", metavar="URL", nargs="*", help="giveinto.me file URL(s) or ids")
    p.add_argument("-o", "--outdir", default=".", help="target directory")
    p.add_argument("--from-catalogue", action="store_true",
                   help="take files from the catalogue (one API request) instead of URLs")
    p.add_argument("--tier", help="--from-catalogue filter, e.g. Free")
    p.add_argument("--type", dest="ftype", help="--from-catalogue filter, e.g. 'Hypno: Full'")
    p.add_argument("--limit", type=int, default=0, help="stop after N files")
    p.add_argument("--name", choices=("remote", "title"), default="remote",
                   help="basename from the CDN filename, already '[F4M] Some Title' "
                        "(default), or from the title plus audience")
    p.add_argument("--all-variants", action="store_true",
                   help="every variant, not just the primary one")
    p.add_argument("--format", choices=("auto", "opus"), default="auto",
                   help="auto prefers mp3 and falls back to opus (default); "
                        "opus forces the opus")
    p.add_argument("--extras", action="store_true",
                   help="also fetch haptics audio, lossless and cover when available")
    p.add_argument("--no-haptics", action="store_true", help="skip the funscript")
    p.add_argument("-f", "--force", action="store_true", help="re-download existing files")
    p.add_argument("-n", "--dry-run", action="store_true", help="show what would happen")
    args = p.parse_args()

    ids = [m.group(1) for m in (re.search(r"([A-Za-z0-9]{4}-[A-Za-z0-9-]+)$", u.rstrip("/"))
                                for u in args.urls) if m]
    if args.from_catalogue:
        try:
            items = api("/list/full?getFilters=1")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            print(f"catalogue fetch failed: {exc}", file=sys.stderr)
            return 1
        rows = []
        for it in items if isinstance(items, list) else []:
            meta = it.get("meta") or {}
            tier = TIERS.get(meta.get("min_tier"), meta.get("min_tier"))
            if args.tier and str(tier).lower() != args.tier.lower():
                continue
            if args.ftype and (it.get("type") or "").lower() != args.ftype.lower():
                continue
            rows.append(it)
        rows.sort(key=lambda r: ((r.get("meta") or {}).get("latest_release") or ""),
                  reverse=True)
        print(f"{len(rows)} files match")
        ids += [r["first_variant_id"] for r in rows if r.get("first_variant_id")]
    if not ids:
        p.error("give at least one URL, or --from-catalogue")
    if args.limit:
        ids = ids[: args.limit]

    seen: set[str] = set()
    failed = sum(0 if handle(i, args, seen) else 1 for i in ids)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
