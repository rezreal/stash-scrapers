#!/usr/bin/env python3
"""Set the giveinto.me URL on Stash scenes, so the URL scraper can identify them.

Scenes added from disk have no URL, so sceneByURL has nothing to match on. This maps each
scene's filename back to its catalogue entry and writes scene.urls; after that Stash's
Identify task can run the Give Into Me scraper across all of them unattended.

    ./giveinto-link.py --stash-url https://odroid/stash --path-contains earesistible
    ./giveinto-link.py --stash-url https://odroid/stash --path-contains earesistible --apply

Dry run by default: it prints every pairing it would make and every file it could not
match, and writes nothing until --apply.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import socket
import ssl
import sys
import urllib.error
import urllib.request

UA = "Mozilla/5.0 (X11; Linux x86_64) giveinto-link/1.0"
API = "https://giveinto.me/api"
SITE = "https://giveinto.me"


def norm(text: str) -> str:
    """Compare titles ignoring punctuation. Filenames lose characters that are illegal
    on Windows shares — 'Who Do You Want Me To Be?' is saved without its '?'."""
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def catalogue() -> list[dict]:
    req = urllib.request.Request(f"{API}/list/full?getFilters=1",
                                 headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        items = json.loads(resp.read().decode("utf-8", "replace"))
    return items if isinstance(items, list) else []


def graphql(args: argparse.Namespace, query: str, variables: dict | None = None) -> dict:
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    headers = {"Content-Type": "application/json", "User-Agent": UA}
    key = args.api_key or os.environ.get("STASH_API_KEY")
    if key:
        headers["ApiKey"] = key
    ctx = None
    if args.insecure:
        # The box this was written for serves an expired certificate whose only SAN is a
        # name that does not resolve off-LAN, so verification cannot succeed. Opt-in only.
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(args.stash_url.rstrip("/") + "/graphql",
                                 data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
        payload = json.loads(resp.read().decode("utf-8", "replace"))
    if payload.get("errors"):
        raise RuntimeError(json.dumps(payload["errors"])[:400])
    return payload.get("data") or {}


SCENES_Q = """
query($page: Int!, $per_page: Int!) {
  findScenes(filter: {page: $page, per_page: $per_page}) {
    count
    scenes { id title urls files { path } }
  }
}
"""

UPDATE_M = """
mutation($id: ID!, $urls: [String!]) {
  sceneUpdate(input: {id: $id, urls: $urls}) { id urls }
}
"""


def build_index(items: list[dict]) -> dict[str, list[dict]]:
    index: dict[str, list[dict]] = {}
    for it in items:
        key = norm(it.get("title") or "")
        if key:
            index.setdefault(key, []).append(it)
    return index


def match(stem: str, index: dict[str, list[dict]], items: list[dict]) -> dict | None:
    """A saved filename back to its catalogue entry.

    Names come from the creator's CDN, which prefixes most but not all with an audience
    tag — '[F4M] Some Title', but also a bare 'Earesistible Takeover'.
    """
    # "_LOSSLESS" marks a lossless copy of a file the catalogue lists under its plain title.
    stem = re.sub(r"[_ ]lossless$", "", stem, flags=re.I)
    m = re.match(r"^\[([^\]]+)\]\s*(.*)$", stem)
    audience, title = (m.group(1), m.group(2)) if m else (None, stem)
    key = norm(title)
    if not key:
        return None
    candidates = index.get(key) or [
        it for it in items if norm(it.get("title") or "").startswith(key)]
    if not candidates and " - " in title:
        # "Throb and Hold - Normal Binaurals": a variant name appended to a real title.
        # Exact match on the head only, so this cannot turn into a loose prefix guess.
        candidates = index.get(norm(title.split(" - ", 1)[0])) or []
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1 and audience:
        narrowed = [c for c in candidates
                    if audience.lower() in " ".join(
                        (c.get("meta") or {}).get("audiences") or []).lower()]
        if len(narrowed) == 1:
            return narrowed[0]
    return None


def suggest(stem: str, items: list[dict]) -> str:
    """Closest catalogue title, shown but never applied — a typo is not a match."""
    key = norm(re.sub(r"^\[[^\]]+\]\s*", "", re.sub(r"[_ ]lossless$", "", stem, flags=re.I)))
    best = max(items, key=lambda i: difflib.SequenceMatcher(
        None, norm(i.get("title") or ""), key).ratio(), default=None)
    if not best:
        return ""
    ratio = difflib.SequenceMatcher(None, norm(best.get("title") or ""), key).ratio()
    return f"  (closest: {best['title']!r}, {ratio:.2f})" if ratio >= 0.6 else ""


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--stash-url", required=True,
                   help="base URL of Stash, e.g. https://odroid/stash")
    p.add_argument("--api-key", help="Stash API key (or set STASH_API_KEY)")
    p.add_argument("--path-contains", default="",
                   help="only touch scenes whose file path contains this, so the run "
                        "cannot stray into the rest of your library")
    p.add_argument("--resolve", metavar="HOST:IP",
                   help="dial IP but keep HOST in the URL and TLS SNI, like curl's "
                        "--resolve. Needed when the server answers only to a name your "
                        "resolver does not know: it rejects an unknown SNI name with "
                        "TLSV1_UNRECOGNIZED_NAME before any certificate is considered.")
    p.add_argument("--insecure", action="store_true",
                   help="skip TLS verification — needed for a self-signed or expired "
                        "certificate")
    p.add_argument("--apply", action="store_true", help="write the URLs (default: dry run)")
    p.add_argument("--force", action="store_true",
                   help="replace a URL the scene already has")
    p.add_argument("--per-page", type=int, default=200)
    args = p.parse_args()

    if args.resolve:
        host, _, ip = args.resolve.partition(":")
        if not ip:
            p.error("--resolve wants HOST:IP")
        real = socket.getaddrinfo

        def pinned(h, port, *a, **kw):
            return real(ip if h == host else h, port, *a, **kw)

        socket.getaddrinfo = pinned
        print(f"resolving {host} -> {ip}", flush=True)

    try:
        items = catalogue()
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"catalogue fetch failed: {exc}", file=sys.stderr, flush=True)
        return 1
    print(f"catalogue: {len(items)} files", flush=True)
    index = build_index(items)

    scenes, page = [], 1
    while True:
        try:
            data = graphql(args, SCENES_Q, {"page": page, "per_page": args.per_page})
        except Exception as exc:
            print(f"stash query failed: {exc}", file=sys.stderr, flush=True)
            return 1
        found = data.get("findScenes") or {}
        batch = found.get("scenes") or []
        scenes += batch
        if len(scenes) >= (found.get("count") or 0) or not batch:
            break
        page += 1
    print(f"stash: {len(scenes)} scenes", flush=True)

    todo, skipped, unmatched, sample_paths = [], [], [], []
    for sc in scenes:
        paths = [f.get("path", "") for f in (sc.get("files") or [])]
        if args.path_contains and not any(
                args.path_contains.lower() in pth.lower() for pth in paths):
            continue
        if not paths:
            continue
        if len(sample_paths) < 3:
            sample_paths.append(paths[0])
        stem = os.path.splitext(os.path.basename(paths[0]))[0]
        hit = match(stem, index, items)
        if not hit:
            unmatched.append(stem + suggest(stem, items))
            continue
        url = f"{SITE}/file/{hit.get('first_variant_id')}"
        if sc.get("urls") and not args.force:
            skipped.append((stem, sc["urls"][0]))
            continue
        todo.append((sc["id"], stem, url))

    if sample_paths:
        print("\n  scenes under the path filter, e.g.:")
        for pth in sample_paths:
            print(f"    {pth}")
    print(f"\n  to set        : {len(todo)}")
    print(f"  already had   : {len(skipped)}   (--force to replace)")
    print(f"  unmatched     : {len(unmatched)}")
    for _, stem, url in todo[:200]:
        print(f"    {stem[:52]:54s} -> {url}")
    for stem in unmatched[:40]:
        print(f"    NO MATCH  {stem}")

    if not args.apply:
        print("\ndry run — nothing written. Re-run with --apply.", flush=True)
        return 0

    done = 0
    for scene_id, stem, url in todo:
        try:
            graphql(args, UPDATE_M, {"id": scene_id, "urls": [url]})
            done += 1
        except Exception as exc:
            print(f"    ! {stem}: {exc}", file=sys.stderr, flush=True)
    print(f"\nset {done} of {len(todo)} scene urls", flush=True)
    return 0 if done == len(todo) else 1


if __name__ == "__main__":
    sys.exit(main())
