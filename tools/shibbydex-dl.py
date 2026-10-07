#!/usr/bin/env python3
"""Download a shibbydex.com file: the audio, its funscript, its buttplug pattern, its cover.

Stash's scraper interface returns metadata only — it cannot write files — so this lives
outside the scraper. Everything is saved under one shared basename, which is what Stash
keys on when it flags a scene as interactive. By default that basename is the site's own
filename, taken from the Content-Disposition of the download redirect:

    <dir>/[F4M] - Alpine Spa & Massage Retreat - [Massage][Anal].mp3
    <dir>/[F4M] - Alpine Spa & Massage Retreat - [Massage][Anal].funscript
    <dir>/[F4M] - Alpine Spa & Massage Retreat - [Massage][Anal].buttplug.funscript
                                   (with --all-haptics)
    <dir>/[F4M] - Alpine Spa & Massage Retreat - [Massage][Anal].png

Only the plain .funscript is one Stash reads; the rest are for other players. Which kinds
a file has is read off the <audio> element rather than hardcoded — funscript, buttplug and
stroker have all been seen.

Files above the Free tier answer 403 to anonymous requests and are skipped.

    ./shibbydex-dl.py https://shibbydex.com/file/<uuid> -o ~/stash/shibby --variations
"""

from __future__ import annotations

import argparse
import html
import hashlib
import http.cookiejar
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (X11; Linux x86_64) shibbydex-dl/1.0"
# Patreon-gated files need your own logged-in session. The site authenticates with a
# Laravel cookie (__Secure-shibbydex_session); export cookies.txt from the browser where
# you are logged in, or put the raw Cookie header value in $SHIBBYDEX_COOKIE.
COOKIE_NAME = "shibbydex-cookies.txt"
COOKIE_ENV = "SHIBBYDEX_COOKIE"
OPENER = urllib.request.build_opener()
SESSION_TIER = 0
COOKIES_LOADED = False
WARNED_ANON = False
BASE = "https://shibbydex.com"
HOST = "shibbydex.com"
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
# Haptic kinds are not hardcoded: the <audio> element advertises them as data-* attributes
# holding /haptic/<uuid>/<kind> URLs (data-funscript, data-butt, data-stroker, ...), and
# the kind is the last path segment. "funscript" keeps the bare .funscript extension
# because that is the one Stash pairs with a media file; every other kind is suffixed,
# e.g. stroker -> ".stroker.funscript".
HAPTIC_URL = re.compile(r"/haptic/[0-9a-f-]{36}/([a-z0-9_-]+)/?$")
# The /files listing, newest first, 90 cards per page. Each card carries everything the
# index needs except the haptic kinds, which only the file page states.
LISTING = BASE + "/files?page=%d"
# Only /file/ links — a card also carries its thumbnail's asset UUID, which is not a file.
FILE_HREF = re.compile(r"shibbydex\.com/file/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")
INDEX_NAME = "shibbydex-index.json"
DATE_DMY = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")
# Illegal on Windows/SMB shares; the site's own filenames are otherwise left intact.
ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
MAX_BASE = 180
# The site rate-limits concurrent requests; everything here is deliberately sequential.
DELAY = 0.4


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None


def remote_basename(uuid: str) -> str | None:
    """The site's own filename for a file, without downloading it.

    /download/<uuid> answers 302 to a signed R2 URL carrying the name in its
    response-content-disposition parameter, so one un-followed request is enough.
    """
    opener = urllib.request.build_opener(NoRedirect, *OPENER.handlers)
    req = urllib.request.Request(f"{BASE}/download/{uuid}", headers={"User-Agent": UA})
    try:
        try:
            resp = opener.open(req, timeout=60)
            loc = resp.headers.get("Location")
            disp = resp.headers.get("Content-Disposition")
        except urllib.error.HTTPError as e:
            if e.code not in (301, 302, 303, 307, 308):
                return None
            loc = e.headers.get("Location")
            disp = e.headers.get("Content-Disposition")
    except (urllib.error.URLError, OSError):
        return None

    if not disp and loc:
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)
        disp = (qs.get("response-content-disposition") or [None])[0]
    if not disp:
        return None
    m = re.search(r'filename\*?=(?:"([^"]+)"|([^;]+))', disp)
    if not m:
        return None
    return sanitize(os.path.splitext(urllib.parse.unquote(m.group(1) or m.group(2)).strip())[0])


def sanitize(name: str) -> str:
    name = ILLEGAL.sub("-", html.unescape(name))
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:MAX_BASE].strip(" .")


def cookie_jar(path: str) -> tuple[http.cookiejar.CookieJar, int]:
    """Lenient Netscape cookies.txt reader.

    Also accepts a plain "name=value" / "name: value" per line, or a whole Cookie header,
    because that is what people actually paste out of devtools.

    http.cookiejar.MozillaCookieJar refuses a file with no "# Netscape HTTP Cookie File"
    header, and asserts that a leading dot on the domain agrees with the second column —
    so a hand-written file usually fails. This accepts either, and the "#HttpOnly_" prefix
    browsers put on session cookies. The cookies still go into a jar rather than a raw
    header so they stay scoped to shibbydex.com and are not sent on to the R2 redirect.
    """
    jar = http.cookiejar.CookieJar()
    count = 0
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.rstrip("\n").rstrip("\r")
            if line.startswith("#HttpOnly_"):
                line = line[len("#HttpOnly_"):]
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            parts = line.split("\t") if "\t" in line else line.split()
            if len(parts) < 7:
                # Also accept the simpler forms people actually paste out of devtools:
                # a whole "Cookie:" header, or one "name=value" / "name: value" per line.
                for pair in re.split(r";\s*", line):
                    m = re.match(r"\s*(?:Cookie:\s*)?([\w.%!#$&'*+^`|~-]+)\s*[:=]\s*(\S+)\s*$",
                                 pair)
                    if m:
                        jar.set_cookie(http.cookiejar.Cookie(
                            version=0, name=m.group(1), value=m.group(2),
                            port=None, port_specified=False,
                            domain=HOST, domain_specified=False, domain_initial_dot=False,
                            path="/", path_specified=True, secure=True, expires=None,
                            discard=False, comment=None, comment_url=None, rest={}))
                        count += 1
                continue
            domain, _sub, cpath, secure, expires, name, value = parts[:7]
            dot = domain.startswith(".")
            jar.set_cookie(http.cookiejar.Cookie(
                version=0, name=name, value=value, port=None, port_specified=False,
                domain=domain, domain_specified=dot, domain_initial_dot=dot,
                path=cpath or "/", path_specified=True,
                secure=secure.upper() == "TRUE",
                expires=int(expires) if expires.lstrip("-").isdigit() else None,
                discard=False, comment=None, comment_url=None, rest={}))
            count += 1
    return jar, count


def load_cookies(explicit: str | None = None) -> str:
    """Wire cookies into OPENER. Returns a one-line description of what was loaded."""
    global OPENER, COOKIES_LOADED
    raw = os.environ.get(COOKIE_ENV)
    if raw:
        OPENER = urllib.request.build_opener()
        OPENER.addheaders = [("User-Agent", UA), ("Cookie", raw)]
        COOKIES_LOADED = True
        return f"cookies: ${COOKIE_ENV}"

    here = os.path.dirname(os.path.abspath(__file__))
    candidate = os.path.join(here, COOKIE_NAME)
    path = explicit or (candidate if os.path.exists(candidate) else None)
    if not path:
        return (f"cookies: none — looked for {candidate} and ${COOKIE_ENV}; "
                "anonymous, Free-tier files only")

    try:
        jar, count = cookie_jar(path)
    except OSError as exc:
        return f"cookies: could not read {path}: {exc}"
    if not count:
        return f"cookies: {path} held no usable lines (need 7 tab-separated fields)"
    for c in jar:
        if re.search(r"%[0-9A-Fa-f]?$", c.value):
            print(f"warning: cookie {c.name} ends in an incomplete percent-escape "
                  f"({c.value[-3:]!r}) — the value looks truncated, re-copy it",
                  file=sys.stderr)
    OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    OPENER.addheaders = [("User-Agent", UA)]
    COOKIES_LOADED = True
    return f"cookies: {count} from {path}"


def fingerprint(uuid: str) -> tuple[str, str] | None:
    """(size, hash of a 256KB slice) for a file, without downloading it.

    The site stores every variant as its own object even when the audio is byte-identical
    — three variants of "The Nymphs of Latex Lake" are the same 88,501,919 bytes. One
    ranged request is enough to tell a real alternate cut from a duplicate.
    """
    req = urllib.request.Request(
        f"{BASE}/download/{uuid}",
        headers={"User-Agent": UA, "Range": "bytes=1000000-1262143"})
    try:
        with OPENER.open(req, timeout=90) as resp:
            chunk = resp.read()
            size = (resp.headers.get("Content-Range") or "/").split("/")[-1]
    except (urllib.error.URLError, OSError):
        return None          # unknown — never a reason to skip a download
    if not chunk or not size.isdigit():
        return None
    return size, hashlib.md5(chunk).hexdigest()


def index_path(explicit: str | None = None) -> str:
    if explicit:
        return explicit
    here = os.path.dirname(os.path.abspath(__file__))
    if os.access(here, os.W_OK):
        return os.path.join(here, INDEX_NAME)
    return os.path.join(tempfile.gettempdir(), INDEX_NAME)


def parse_listing(page: str) -> list[dict]:
    """One dict per file card on a /files page."""
    cards = []
    for chunk in re.split(r'<div class="card file-card"', page)[1:]:
        chunk = chunk.split("<div class=\"card file-card\"")[0]
        uuids = list(dict.fromkeys(FILE_HREF.findall(chunk)))
        if not uuids:
            continue
        title = re.search(r'class="card-link"[^>]*>(.*?)</a>', chunk, re.S)
        blurb = re.search(r'class="[^"]*file-card-text[^"]*"[^>]*>(.*?)</p>', chunk, re.S)
        spans = [html.unescape(re.sub(r"<[^>]+>", " ", m.group(1))).strip()
                 for m in re.finditer(r"<span[^>]*>(.*?)</span>", chunk, re.S)]
        spans = [re.sub(r"\s+", " ", s) for s in spans if s.strip()]

        entry: dict = {"uuid": uuids[0], "url": f"{BASE}/file/{uuids[0]}"}
        if title:
            entry["title"] = html.unescape(re.sub(r"<[^>]+>", "", title.group(1))).strip()
        if blurb:
            entry["description"] = re.sub(
                r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", blurb.group(1)))).strip()
        for value in spans:
            m = DATE_DMY.match(value)
            if m:
                entry["date"] = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
            elif re.fullmatch(r"(?:\d+h )?(?:\d+m )?\d+s", value):
                entry["duration"] = value
            elif re.fullmatch(r"[\d,]+", value):
                entry["play_count"] = int(value.replace(",", ""))
            elif value in ("Free", "Hypnosub", "Hypnoslave", "Devoted Pet"):
                entry["tier"] = value
            elif re.fullmatch(r"F4[A-Z]{1,2}|N/A", value):
                entry["audience"] = value
        # Sibling variations are the pill links inside the card.
        entry["variations"] = uuids[1:]
        cards.append(entry)
    return cards


def build_index(path: str, update: bool) -> dict:
    """Crawl /files into {uuid: card}. update=True stops at the first page of known files."""
    files: dict = {}
    if update:
        try:
            files = json.load(open(path, encoding="utf-8")).get("files", {})
            print(f"updating {len(files)} known files")
        except (OSError, ValueError):
            print("no usable index yet, building from scratch")

    page = 1
    added = 0
    while True:
        try:
            markup = fetch(LISTING % page)
        except (urllib.error.URLError, OSError) as exc:
            print(f"  page {page} failed: {exc}", file=sys.stderr)
            break
        cards = parse_listing(markup)
        if not cards:
            break
        fresh = [c for c in cards if c["uuid"] not in files]
        for card in cards:
            files[card["uuid"]] = card
        added += len(fresh)
        print(f"  page {page:2d}: {len(cards):3d} cards, {len(fresh):3d} new")
        # The listing is newest first, so an update can stop once a whole page is known.
        if update and not fresh:
            print("  reached already-indexed files, stopping")
            break
        page += 1
        time.sleep(DELAY)

    blob = {"fetched": time.time(), "files": files}
    try:
        json.dump(blob, open(path, "w", encoding="utf-8"))
        print(f"{len(files)} files indexed ({added} new) -> {path}")
    except OSError as exc:
        print(f"could not write {path}: {exc}", file=sys.stderr)
    return blob


def select(blob: dict, args: argparse.Namespace) -> list[dict]:
    rows = list(blob.get("files", {}).values())
    if args.tier:
        rows = [r for r in rows if (r.get("tier") or "").lower() == args.tier.lower()]
    if args.audience:
        rows = [r for r in rows if (r.get("audience") or "").lower() == args.audience.lower()]
    if args.match:
        pat = re.compile(args.match, re.I)
        rows = [r for r in rows
                if pat.search(r.get("title", "")) or pat.search(r.get("description", ""))]
    if args.since:
        rows = [r for r in rows if r.get("date", "") >= args.since]
    rows.sort(key=lambda r: (r.get("date", ""), r.get("title", "")), reverse=True)
    if args.limit:
        rows = rows[: args.limit]
    return rows


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with OPENER.open(req, timeout=60) as resp:
        return resp.read().decode("utf-8", "replace")


def attrs(tag: str) -> dict[str, str]:
    return {
        m.group(1).lower(): html.unescape(m.group(2))
        for m in re.finditer(r'([\w:-]+)\s*=\s*"([^"]*)"', tag)
    }


def dd_after(page: str, label: str) -> str | None:
    """Value of a <dt>label</dt><dd>value</dd> pair in the details list."""
    m = re.search(
        r"<dt[^>]*>\s*%s\s*</dt>\s*<dd[^>]*>(.*?)</dd>" % re.escape(label), page, re.S | re.I
    )
    if not m:
        return None
    return html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()


def player_data(page: str) -> dict:
    """The <script type="application/json" id="sd-player-data"> payload, if present."""
    m = re.search(r'<script type="application/json" id="sd-player-data">(.*?)</script>',
                  page, re.S)
    if not m:
        return {}
    try:
        queue = json.loads(m.group(1)).get("queue") or []
    except (json.JSONDecodeError, AttributeError):
        return {}
    return (queue[0].get("file") or {}) if queue else {}


def parse(page: str, uuid: str) -> dict:
    info: dict = {
        "uuid": uuid,
        "title": None,
        "tier": dd_after(page, "Tier"),
        "audience": dd_after(page, "Audience"),
        "date": dd_after(page, "Release Date"),
        "haptics": {},
        "cover": None,
        "video": bool(re.search(r'<iframe\b[^>]*id="video_frame"', page)),
        # Decide from what the page actually offers this session, not from Tier. There
        # are two different lock messages — "Connect to Patreon to unlock" when logged
        # out, "Upgrade tier to unlock (X)" when logged in below the required tier — so
        # detect the download link positively instead of matching either phrase.
        "downloadable": f"/download/{uuid}" in page,
        "lock_reason": (re.search(r">\s*((?:Connect to Patreon|Upgrade tier) to unlock[^<]*)",
                                  page) or [None, None])[1],
        "variations": [],
    }

    m = re.search(r"<h1[^>]*>(.*?)</h1>", page, re.S)
    if m:
        info["title"] = html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()

    # Late 2026 the site replaced its <audio> element with a JS player whose data lives
    # in a JSON blob. That blob is only emitted for a file the session may actually play,
    # so its absence is itself a reliable "not playable" signal.
    player = player_data(page)
    for kind, href in (player.get("haptics") or {}).items():
        if href:
            info["haptics"][kind] = href
    if player.get("tierName"):
        info["tier"] = player["tierName"]
    if isinstance(player.get("duration"), int):
        info["duration"] = player["duration"]

    if not info["haptics"]:
        # Pre-rewrite markup, kept so older pages still work.
        m = re.search(r"<audio\b[^>]*>", page, re.S)
        if m:
            for key, val in attrs(m.group(0)).items():
                if not key.startswith("data-"):
                    continue
                kind = HAPTIC_URL.search(val)
                if kind:
                    info["haptics"][kind.group(1)] = val

    for m in re.finditer(r"<img\b[^>]*>", page):
        a = attrs(m.group(0))
        if "img-artwork" in a.get("class", "") and "assets.shibbydex.com" in a.get("src", ""):
            info["cover"] = a["src"]
            break

    # Sibling variations: the audience buttons above the details toggle. The current file
    # is btn-light, the others btn-outline-light; all of them share one title.
    for m in re.finditer(r"<a\b[^>]*class=\"[^\"]*(?:sd-variant-card|btn-sm)[^\"]*\"[^>]*>", page):
        a = attrs(m.group(0))
        u = UUID.search(a.get("href", ""))
        if u:
            info["variations"].append(
                {"uuid": u.group(0), "label": a.get("title", ""), "current": u.group(0) == uuid}
            )
    return info


def slugify(text: str) -> str:
    text = html.unescape(text or "").replace("&", "and")
    text = re.sub(r"[^\w\s-]", "", text).strip()
    return re.sub(r"[-\s]+", "-", text) or "untitled"


def basename_for(info: dict, mode: str) -> str:
    if mode == "uuid":
        return info["uuid"]
    if mode == "site":
        remote = remote_basename(info["uuid"])
        time.sleep(DELAY)
        if remote:
            return remote
        print("    - no filename on the download redirect, falling back to the title")
    base = slugify(info["title"])
    # Variations share a title, so the audience is what tells the files apart on disk.
    if len(info["variations"]) > 1 and info["audience"]:
        base += "-" + slugify(info["audience"])
    return base


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
        with OPENER.open(req, timeout=300) as resp, open(tmp, "wb") as fh:
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


def handle(uuid: str, args: argparse.Namespace, seen: set[str],
           claimed: dict[str, str] | None = None,
           fingerprints: dict | None = None) -> bool:
    if claimed is None:
        claimed = {}
    if fingerprints is None:
        fingerprints = {}
    if uuid in seen:
        return True
    seen.add(uuid)

    url = f"{BASE}/file/{uuid}"
    print(url)
    try:
        page = fetch(url)
    except (urllib.error.URLError, OSError) as exc:
        print(f"    ! fetch failed: {exc}", file=sys.stderr)
        return False
    time.sleep(DELAY)

    global SESSION_TIER, WARNED_ANON
    m = re.search(r'<body[^>]*data-user-tier="(\d+)"', page)
    if m and int(m.group(1)) != SESSION_TIER:
        SESSION_TIER = int(m.group(1))
        print(f"    (site reports data-user-tier={SESSION_TIER})")
    if m and m.group(1) == "0" and COOKIES_LOADED and not WARNED_ANON:
        WARNED_ANON = True
        print("    ! cookies were loaded but the site reports data-user-tier=0 — the "
              "session is not being accepted. Re-copy the cookie value (check it is "
              "complete and unexpired); note $SHIBBYDEX_COOKIE overrides the file.")

    info = parse(page, uuid)
    label = f" [{info['audience']}]" if info["audience"] else ""
    print(f"    {info['title'] or '?'}{label} — {info['date'] or '?'} — tier: {info['tier'] or '?'}")

    ok = True
    if info["video"]:
        # 13 of 100 sampled files are an embedded video rather than an audio file;
        # /play and /download both answer 404 for them. Not an error — audio only.
        print("    - video file, skipped (audio only)")
    elif not info["downloadable"]:
        why = (info["lock_reason"] or "locked for this session").strip()
        print(f"    ! {why} — skipped")
        ok = False
    else:
        os.makedirs(args.outdir, exist_ok=True)
        if not args.keep_duplicates:
            print_ = fingerprint(uuid)
            if print_ and print_ in fingerprints:
                print(f"    = identical audio to {fingerprints[print_]} — skipped")
                return ok
            if print_:
                fingerprints[print_] = None   # filled in once the name is known

        stem = basename_for(info, args.name)
        # The site's own filename usually carries the audience ("[F4M] - ..."), but not
        # always; make sure two variations in one run cannot claim the same basename.
        if claimed.get(stem, uuid) != uuid and info["audience"]:
            stem = f"{stem} [{sanitize(info['audience'])}]"
        claimed[stem] = uuid
        if not args.keep_duplicates and print_:
            fingerprints[print_] = stem
        base = os.path.join(args.outdir, stem)
        ok &= download(f"{BASE}/download/{uuid}", base + ".mp3", args.force, args.dry_run)
        if not args.no_haptics:
            for kind, href in sorted(info["haptics"].items()):
                if kind not in ("funscript", "vibe") and not args.all_haptics:
                    continue
                # Stash pairs a scene with "<media>.funscript", so the primary pattern
                # gets the bare suffix. The site renamed funscript->vibe and
                # buttplug->plug in the rewrite; all of them return identical JSON on
                # every file checked, bar stroker which is genuinely different.
                primary = kind in ("funscript", "vibe")
                ext = ".funscript" if primary else f".{kind}.funscript"
                ok &= download(href, base + ext, args.force, args.dry_run)
            if not info["haptics"]:
                print("    - no haptics on this file")
            elif not args.all_haptics and len(info["haptics"]) > 1:
                skipped = sorted(k for k in info["haptics"]
                                 if k not in ("funscript", "vibe"))
                print(f"    - {', '.join(skipped)} available too (--all-haptics)")
        if args.cover:
            if info["cover"]:
                ext = os.path.splitext(urllib.parse.urlparse(info["cover"]).path)[1] or ".png"
                ok &= download(info["cover"], base + ext, args.force, args.dry_run)
            else:
                print("    - placeholder artwork only, no cover to fetch")

    if args.variations:
        for v in info["variations"]:
            if v["current"]:
                continue
            if "video" in v["label"].lower():
                print(f"    - variation '{v['label']}' is a video, skipped")
                seen.add(v["uuid"])
                continue
            ok &= handle(v["uuid"], args, seen, claimed, fingerprints)
    return ok


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("urls", metavar="URL", nargs="*", help="shibbydex file URL(s) or bare UUID(s)")
    p.add_argument("--build-index", action="store_true",
                   help="crawl every /files page into a local index and exit")
    p.add_argument("--update-index", action="store_true",
                   help="add newly published files to the index and exit (stops at the "
                        "first page already fully known)")
    p.add_argument("--from-index", action="store_true",
                   help="download from the index instead of from URLs on the command line")
    p.add_argument("--index", metavar="PATH", help=f"index location (default: {INDEX_NAME} "
                   "next to this script)")
    p.add_argument("--cookies", metavar="PATH",
                   help=f"Netscape cookies.txt for a logged-in session, to reach "
                        f"Patreon-tier files (default: {COOKIE_NAME} next to this script, "
                        f"or ${COOKIE_ENV})")
    p.add_argument("--tier", help="--from-index filter, e.g. Free")
    p.add_argument("--audience", help="--from-index filter, e.g. F4M")
    p.add_argument("--match", metavar="REGEX", help="--from-index filter on title/description")
    p.add_argument("--since", metavar="YYYY-MM-DD", help="--from-index filter on release date")
    p.add_argument("--limit", type=int, help="--from-index: stop after N files")
    p.add_argument("--list", action="store_true", help="--from-index: print the selection, "
                   "do not download")
    p.add_argument("-o", "--outdir", default=".", help="target directory (default: .)")
    p.add_argument("--name", choices=("site", "title", "uuid"), default="site",
                   help="basename source: the site's own filename from the download "
                        "redirect (default), a slug of the title, or the file UUID")
    p.add_argument("--variations", action="store_true",
                   help="also download the file's sibling variations (F4M, F4A, Video, ...)")
    p.add_argument("--cover", action="store_true", help="also download real cover art, if any")
    p.add_argument("--keep-duplicates", action="store_true",
                   help="download every variant even when the audio is byte-identical to "
                        "one already fetched in this run")
    p.add_argument("--all-haptics", action="store_true",
                   help="save every haptic kind the file advertises, not just the "
                        "funscript: <name>.buttplug.funscript, <name>.stroker.funscript, ...")
    p.add_argument("--no-haptics", action="store_true", help="skip haptic files entirely")
    p.add_argument("-f", "--force", action="store_true", help="re-download existing files")
    p.add_argument("-n", "--dry-run", action="store_true", help="show what would be downloaded")
    args = p.parse_args()

    print(load_cookies(args.cookies))

    path = index_path(args.index)
    if args.build_index or args.update_index:
        build_index(path, update=args.update_index)
        return 0

    urls = list(args.urls)
    if args.from_index:
        try:
            blob = json.load(open(path, encoding="utf-8"))
        except (OSError, ValueError):
            print(f"no index at {path} — run --build-index first", file=sys.stderr)
            return 1
        rows = select(blob, args)
        print(f"{len(rows)} of {len(blob.get('files', {}))} indexed files match")
        if args.list:
            for r in rows:
                print(f"  {r.get('date','?')}  {(r.get('tier') or '?'):<12} "
                      f"{(r.get('audience') or '?'):<5} {(r.get('duration') or '?'):>9}  "
                      f"{r.get('title','?')}")
            return 0
        urls += [r["url"] for r in rows]
    if not urls:
        p.error("give at least one URL, or --from-index, or --build-index")

    seen: set[str] = set()
    claimed: dict[str, str] = {}
    fingerprints: dict = {}
    failed = 0
    for raw in urls:
        m = UUID.search(raw)
        if not m:
            print(f"! not a shibbydex file URL or UUID: {raw}", file=sys.stderr)
            failed += 1
            continue
        if not handle(m.group(0), args, seen, claimed, fingerprints):
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
