#!/usr/bin/env python3
"""Stash scene scraper for shibbydex.com file pages.

Reads {"url": ...} on stdin and writes a scene object on stdout, per Stash's
scrapeScript protocol. Standard library only — no py_common, no lxml.

This exists rather than a pure-XPath YAML scraper because of the trigger tags: a
mapped XPath field has exactly one selector and one postProcess chain, so it cannot
emit "Trigger Installed: X" for one group of badges and "Trigger: Y" for another.
Rendering the <br>-delimited description is also simply correct here, where the YAML
version has to encode line breaks as separator runs and decode them with regexes.
"""

from __future__ import annotations

import difflib
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
from html.parser import HTMLParser

UA = "Mozilla/5.0 (X11; Linux x86_64) shibbydex-scraper/1.0"
BASE = "https://shibbydex.com"
HOST = "shibbydex.com"
ASSET_HOST = "assets.shibbydex.com"
# Files without their own artwork show a shared logo instead. Only three exist — f4a, f4m
# and f4f — and audiences like F4NB and F4TM reuse the f4a one.
PLACEHOLDER = "/images/thumbnail_logo_"
PLACEHOLDER_ART = True

# Series -> Group. A file page does not state which series it belongs to — the only
# /serie/ links that appear on one are prose hyperlinks the author happened to write. The
# mapping only exists in the other direction, so it is built once from /series plus each
# /serie/<uuid> page (32 of them, no pagination on either) and cached. 323 of the 2,041
# files are in a series, 9 of them in two.
# A logged-in session, for anyone with a Patreon subscription. Locked pages already carry
# the full title/description/date/tags anonymously, so this changes little for scraping —
# it is here so the scraper and the downloader read the site the same way.
COOKIE_NAME = "shibbydex-cookies.txt"
COOKIE_ENV = "SHIBBYDEX_COOKIE"
SERIES_TTL = 7 * 24 * 3600
SERIES_DELAY = 0.35        # the site rejects concurrent requests; stay sequential
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr"}
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

# The page's details table, split by how the values actually behave across 100 sampled
# files. Stash has no custom fields, so everything here has to land in tags or details.
#
# Low cardinality and comma-separated -> tags, prefixed so "None"/"Yes"/"Modern" cannot
# collide with the file's own tag badges. Counts are distinct values across the sample,
# before splitting on commas.
TABLE_TAGS = (
    ("Tier", "Tier"),                # 4 distinct
    ("Level", "Level"),              # 4
    ("Background", "Background"),    # 6
    ("Consent", "Consent"),          # 14
    ("DS", "D/s"),                   # 15
    ("Effects", "Effects"),          # 23
    ("Orgasm", "Orgasm"),            # 31
    ("Setting", "Setting"),          # 36
    ("Author", "Author"),            # 36 — the script writer, distinct from Artist
    ("Tone", "Tone"),                # 41
    ("Wakener", "Wakener"),          # 4
    ("Aftercare", "Aftercare"),      # 3
)
# Free prose, 150-230 characters on average — useless as tags, worth having in details.
TABLE_DETAILS = ("Induction", "Deepener", "Body", "Instructions", "Intended Effect")
# Placeholders that mean "nothing here"; "No" is kept, it is a real answer.
EMPTY = {"", "-", "n/a", "na", "none", "no info", "unknown"}
# Some table cells hold a sentence rather than a label — one file answers "Orgasm" with a
# clause. Anything this long is prose and does not belong in a tag.
MAX_TAG_VALUE = 40
# Skipped on purpose: Release Date and Audience are already fields, Play Count changes on
# every view, Genitalia was "N/A" on all 100 sampled files, and Script / Reddit / Haptic
# Script are cross-references to other files rather than facts about this one.


class Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag: str, attrs: dict | None = None, parent: "Node | None" = None):
        self.tag = tag
        self.attrs = attrs or {}
        self.children: list = []          # Node | str
        self.parent = parent

    def has_class(self, name: str) -> bool:
        return name in (self.attrs.get("class") or "").split()

    def iter(self):
        yield self
        for c in self.children:
            if isinstance(c, Node):
                yield from c.iter()

    def find_all(self, tag: str | None = None, cls: str | None = None) -> list["Node"]:
        return [n for n in self.iter()
                if (tag is None or n.tag == tag) and (cls is None or n.has_class(cls))]

    @property
    def text(self) -> str:
        """Text content, collapsed to single spaces."""
        out = []
        for c in self.children:
            out.append(c if isinstance(c, str) else c.text)
        return re.sub(r"\s+", " ", "".join(out)).strip()

    @property
    def lines(self) -> str:
        """Text content with <br> kept as newlines and inline markup kept inline."""
        out: list[str] = []

        def walk(n: Node):
            for c in n.children:
                if isinstance(c, str):
                    out.append(c)
                elif c.tag == "br":
                    out.append("\n")
                else:
                    walk(c)

        walk(self)
        text = re.sub(r"[^\S\n]+", " ", "".join(out))
        text = re.sub(r" *\n *", "\n", text)
        return re.sub(r"\n{3,}", "\n\n", text).strip()


class Tree(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root")
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        node = Node(tag, {k: (v or "") for k, v in attrs}, self.cur)
        self.cur.children.append(node)
        if tag not in VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        self.cur.children.append(Node(tag, {k: (v or "") for k, v in attrs}, self.cur))

    def handle_endtag(self, tag):
        node = self.cur
        while node is not self.root:
            if node.tag == tag:
                self.cur = node.parent
                return
            node = node.parent

    def handle_data(self, data):
        if data:
            self.cur.children.append(data)


def parse(markup: str) -> Node:
    t = Tree()
    t.feed(markup)
    return t.root


def player_data(markup: str) -> dict:
    """The <script type="application/json" id="sd-player-data"> payload.

    The site replaced its <audio> element with a JS player in late 2026; this blob is
    where the file's duration, tier and haptic URLs now live. It is only emitted for a
    file the current session may play, so locked files and video embeds have none.
    """
    m = re.search(r'<script type="application/json" id="sd-player-data">(.*?)</script>',
                  markup, re.S)
    if not m:
        return {}
    try:
        queue = (json.loads(m.group(1)).get("queue") or [])
    except (json.JSONDecodeError, AttributeError):
        return {}
    return (queue[0].get("file") or {}) if queue else {}


def split_values(value: str) -> list[str]:
    """Split a comma-separated field without breaking inside parentheses.

    "Vanilla (Shibby, Good Boy)" is one value, not two — splitting naively produced the
    tags "D/s: Vanilla (Shibby" and "D/s: Good Boy)".
    """
    return [v.strip() for v in re.split(r",\s*(?![^()]*\))", value) if v.strip()]


def definition(root: Node, label: str) -> str | None:
    """Value of a <dt>label</dt><dd>value</dd> pair."""
    for dl in root.find_all("dl"):
        kids = [c for c in dl.children if isinstance(c, Node)]
        for i, node in enumerate(kids):
            if node.tag == "dt" and node.text == label:
                for nxt in kids[i + 1:]:
                    if nxt.tag == "dd":
                        return nxt.text or None
                    if nxt.tag == "dt":
                        break
    return None


def lead_blocks(root: Node) -> list[tuple[str, str]]:
    """Every <p class="lead"> paired with the heading above it.

    The first is the description; pages carry up to four more under Credits, Notes,
    Disclaimer, Haptics or Warning headings, and all of those are per-file text rather
    than boilerplate, so they are kept and labelled.
    """
    flat = [n for n in root.iter() if n.tag in ("h1", "h2", "h3", "h4", "p")]
    blocks, heading = [], ""
    for node in flat:
        if node.tag != "p":
            heading = node.text
        elif node.has_class("lead"):
            body = node.lines
            if body:
                blocks.append((heading, body))
            heading = ""
    return blocks


def section_named(root: Node, heading: str) -> Node | None:
    for sec in root.find_all("section"):
        if any(h.text == heading for h in sec.find_all("h2")):
            return sec
    return None


def variations(root: Node) -> list[tuple[str, str]]:
    """The audience buttons above the details toggle: (label, url) for each sibling file.

    Variations are separate UUIDs sharing one title — audience cuts (F4A / F4M / F4TF),
    Naked Audio, Video. 77 of 100 sampled files have at least two.
    """
    out = []
    for a in root.find_all("a"):
        href = a.attrs.get("href", "")
        # a.sd-variant-card since the 2026 player rewrite; btn-sm was the old markup.
        if (a.has_class("sd-variant-card") or a.has_class("btn-sm")) and UUID.search(href):
            out.append((a.attrs.get("title") or a.text, href))
    return out


def trigger_tags(root: Node) -> list[str]:
    """Triggers, prefixed by the <h3> group they sit under.

    Used      -> "Trigger: <name>"
    Installed -> "Trigger Installed: <name>"
    """
    sec = section_named(root, "Triggers")
    if sec is None:
        return []
    prefixes = {"used": "Trigger: ", "installed": "Trigger Installed: "}
    tags, group = [], None
    for node in sec.iter():
        if node.tag == "h3":
            group = prefixes.get(node.text.strip().lower())
        elif group and node.tag == "a" and node.has_class("badge"):
            name = node.text
            if name:
                tags.append(group + name)
    return tags


def scrape(markup: str, url: str) -> dict:
    root = parse(markup)

    detail = next((n for n in root.find_all("div") if n.has_class("file-detail")), root)
    h1 = detail.find_all("h1")

    scene: dict = {"url": url}
    if h1:
        scene["title"] = h1[0].text

    blocks = lead_blocks(root)
    if blocks:
        parts = [blocks[0][1]]
        parts += [f"{head}: {body}" if head else body for head, body in blocks[1:]]
        scene["details"] = "\n\n".join(parts)

    released = definition(root, "Release Date") or ""
    m = re.match(r"^(\d{2})/(\d{2})/(\d{4})$", released)
    if m:
        scene["date"] = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"

    # Cover art, real if the file has any. Most files fall back to one of three
    # placeholder logos (f4a / f4m / f4f — every other audience reuses f4a), and video
    # files have no <img> at all, in which case nothing is returned and the scene keeps
    # whatever cover it already has.
    #
    # The placeholders are webp, which needs no converting: Stash decodes webp
    # (pkg/image/webp.go) and stores a scraped image as a data URI keyed by checksum
    # (pkg/sqlite/blob.go), so every scene sharing a placeholder shares one blob row.
    # Set PLACEHOLDER_ART = False to go back to real art only.
    art = None
    for img in root.find_all("img"):
        if not img.has_class("img-artwork"):
            continue
        src = img.attrs.get("src", "")
        if ASSET_HOST in src:
            art = src
            break
        if PLACEHOLDER_ART and PLACEHOLDER in src and art is None:
            art = src
    if art:
        scene["image"] = art

    scene["studio"] = {"name": "Shibby"}

    uuid = UUID.search(url)
    if uuid:
        scene["code"] = uuid.group(0)

    # Duration only became available when the site moved to its JSON player; the file
    # page never stated a runtime before. Absent for locked files and video embeds.
    duration = player_data(markup).get("duration")
    if isinstance(duration, int) and duration > 0:
        scene["duration"] = duration

    artist = definition(root, "Artist")
    if artist:
        # Some files list the Artist as "N/A", which would otherwise create a performer
        # by that name.
        names = [n for n in split_values(artist) if n.lower() not in EMPTY]
        if names:
            scene["performers"] = [{"name": n} for n in names]

    tags = [a.text for a in root.find_all("a")
            if "/tag/" in a.attrs.get("href", "") and a.has_class("badge")]
    # Variations share one title; the audience is the only field that tells them apart.
    audience = definition(root, "Audience")
    if audience and audience.lower() not in EMPTY:
        tags.append(audience)
    tags += trigger_tags(root)
    for label, prefix in TABLE_TAGS:
        raw = definition(root, label)
        if not raw:
            continue
        if len(raw) > MAX_TAG_VALUE * 2:
            continue
        for value in split_values(raw):
            if value.lower() not in EMPTY and len(value) <= MAX_TAG_VALUE:
                tags.append(f"{prefix}: {value}")

    seen, unique = set(), []
    for t in tags:
        if t and t not in seen:
            seen.add(t)
            unique.append(t)
    if unique:
        scene["tags"] = [{"name": t} for t in unique]

    # Groups are the site's series. Variations deliberately do not make a group — they
    # are the same work in different cuts, told apart by the Audience tag instead.
    if scene.get("code"):
        groups = []
        for serie in series_for(scene["code"], log=lambda m: print(m, file=sys.stderr)):
            group: dict = {"name": serie["name"], "urls": [serie["url"]],
                           "studio": {"name": "Shibby"}}
            if serie.get("synopsis"):
                group["synopsis"] = serie["synopsis"]
            groups.append(group)
        if groups:
            scene["groups"] = groups

    extra = []
    for label in TABLE_DETAILS:
        value = definition(root, label)
        if value and value.lower() not in EMPTY:
            extra.append(f"{label}: {value}")
    if extra:
        scene["details"] = "\n\n".join(filter(None, [scene.get("details"), *extra]))

    return scene


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


# /search?search=X just 302s to /files?search=X, which renders the ordinary file cards.
SEARCH = BASE + "/files?search=%s"
# A downloaded file is named "[F4M] - Title - [Tag][Tag].mp3" by the site, so strip the
# audience prefix and the trailing bracket run to recover the title to search for.
AUDIENCE_PREFIX = re.compile(r"^\s*\[[^\]]{1,12}\]\s*-\s*")
TRAILING_TAGS = re.compile(r"\s*-\s*(?:\[[^\]]*\]|;)+\s*$")
MEDIA_EXT = re.compile(r"\.(?:mp3|m4a|wav|flac|ogg|opus|mp4|mkv|funscript)$", re.I)


def title_from_filename(name: str) -> str:
    """'[F4M] - Alpine Spa & Massage Retreat - [Massage][Anal].mp3' -> the title."""
    name = MEDIA_EXT.sub("", (name or "").strip())
    name = AUDIENCE_PREFIX.sub("", name)
    name = TRAILING_TAGS.sub("", name)
    return re.sub(r"\s+", " ", name).strip()


def search_cards(query: str) -> list[dict]:
    """Search the site and return one entry per matching file card."""
    root = parse(fetch(SEARCH % urllib.parse.quote(query)))
    out = []
    for card in root.find_all("div", "file-card"):
        link = next((a for a in card.find_all("a")
                     if a.has_class("card-link") and "/file/" in a.attrs.get("href", "")), None)
        if link is None:
            continue
        entry = {"title": link.text, "url": link.attrs["href"]}
        blurb = next((p for p in card.find_all("p") if p.has_class("file-card-text")), None)
        if blurb is not None:
            entry["details"] = blurb.text
        for span in card.find_all("span"):
            m = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", span.text)
            if m:
                entry["date"] = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
        img = next((i for i in card.find_all("img") if ASSET_HOST in i.attrs.get("src", "")), None)
        if img is not None:
            # The card shows a 200x200 thumb; the file page has the full-size original.
            entry["image"] = re.sub(r"/thumbs/([0-9a-f-]+)-\d+x\d+", r"/\1", img.attrs["src"])
        out.append(entry)
    return out


def scrape_group(markup: str, url: str) -> dict:
    """A /serie/<uuid> page as a Stash group."""
    root = parse(markup)
    group: dict = {"urls": [url], "studio": {"name": "Shibby"}}

    h1 = root.find_all("h1")
    if h1:
        group["name"] = h1[0].text
    lead = next((p for p in root.find_all("p") if p.has_class("lead")), None)
    if lead is not None:
        group["synopsis"] = lead.lines

    # The page has no artwork of its own, so borrow the first member file that has real
    # cover art — most series have none at all and simply get no image.
    art = next((i.attrs["src"] for i in root.find_all("img")
                if ASSET_HOST in i.attrs.get("src", "")), None)
    if art:
        group["front_image"] = re.sub(r"/thumbs/([0-9a-f-]+)-\d+x\d+", r"/\1", art)

    # Date the series by its earliest release; the page states no date of its own.
    dates = []
    for span in root.find_all("span"):
        m = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", span.text)
        if m:
            dates.append(f"{m.group(3)}-{m.group(2)}-{m.group(1)}")
    if dates:
        group["date"] = min(dates)

    return group


def scene_by_name(name: str) -> list[dict]:
    """Search results for a filename, closest title first.

    The site's search is loose — it matches descriptions too — so an exact title can come
    back below a weaker hit. Reorder so the picker in Stash puts the obvious answer on top.
    """
    query = title_from_filename(name)
    results = search_cards(query)
    if not results and query != name:
        results = search_cards(name)

    def norm(t: str) -> str:
        return re.sub(r"[^a-z0-9 ]", "", (t or "").lower()).strip()

    target = norm(query)
    results.sort(key=lambda r: difflib.SequenceMatcher(None, norm(r["title"]), target).ratio(),
                 reverse=True)
    return results


def opener() -> urllib.request.OpenerDirector:
    """Cookie-aware opener: $SHIBBYDEX_COOKIE, else cookies.txt beside this script."""
    raw = os.environ.get(COOKIE_ENV)
    if raw:
        o = urllib.request.build_opener()
        o.addheaders = [("User-Agent", UA), ("Cookie", raw)]
        return o
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), COOKIE_NAME)
    if os.path.exists(path):
        try:
            jar, count = cookie_jar(path)
            if count:
                o = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
                o.addheaders = [("User-Agent", UA)]
                return o
            print(f"ignoring {path}: no usable cookie lines", file=sys.stderr)
        except OSError as exc:
            print(f"ignoring {path}: {exc}", file=sys.stderr)
    o = urllib.request.build_opener()
    o.addheaders = [("User-Agent", UA)]
    return o


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with opener().open(req, timeout=60) as resp:
        return resp.read().decode("utf-8", "replace")


def cache_path() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    if os.access(here, os.W_OK):
        return os.path.join(here, "shibbydex-series.json")
    return os.path.join(tempfile.gettempdir(), "shibbydex-series.json")


def build_series_index(log=lambda m: None) -> dict:
    """{file uuid: [{"name", "url", "synopsis"}]} for every file in a series."""
    root = parse(fetch(BASE + "/series"))
    listed = []
    for card in root.find_all("div", "card-body"):
        link = next((a for a in card.find_all("a") if "/serie/" in a.attrs.get("href", "")), None)
        if link is None:
            continue
        blurb = next((p for p in card.find_all("p") if p.has_class("card-text")), None)
        listed.append({"name": link.text, "url": link.attrs["href"],
                       "synopsis": blurb.text if blurb is not None else ""})

    index: dict[str, list] = {}
    for i, serie in enumerate(listed, 1):
        time.sleep(SERIES_DELAY)
        try:
            markup = fetch(serie["url"])
        except (urllib.error.URLError, OSError) as exc:
            log(f"  series {i}/{len(listed)} failed: {exc}")
            continue
        uuids = dict.fromkeys(re.findall(r"shibbydex\.com/file/([0-9a-f-]{36})", markup))
        log(f"  {len(uuids):3d} files  {serie['name']}")
        for uuid in uuids:
            index.setdefault(uuid, []).append(serie)
    return index


def series_for(uuid: str, log=lambda m: None) -> list[dict]:
    """Cached series lookup. Any failure returns nothing rather than failing the scrape."""
    path = cache_path()
    try:
        blob = json.load(open(path, encoding="utf-8"))
        if time.time() - blob.get("fetched", 0) < SERIES_TTL:
            return blob.get("index", {}).get(uuid, [])
    except (OSError, ValueError):
        pass
    try:
        index = build_series_index(log)
    except (urllib.error.URLError, OSError) as exc:
        log(f"series index unavailable: {exc}")
        return []
    try:
        json.dump({"fetched": time.time(), "index": index}, open(path, "w", encoding="utf-8"))
    except OSError as exc:
        log(f"could not write {path}: {exc}")
    return index.get(uuid, [])


def main() -> int:
    action = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else ""

    if action == "sceneByName":
        try:
            payload = json.loads(sys.stdin.read() or "{}")
        except json.JSONDecodeError:
            payload = {}
        name = payload.get("name") or payload.get("title") or ""
        if not name:
            print("[]")
            return 1
        try:
            print(json.dumps(scene_by_name(name)))
        except (urllib.error.URLError, OSError) as exc:
            print(f"search failed: {exc}", file=sys.stderr)
            print("[]")
            return 1
        return 0

    if action == "groupByURL":
        try:
            payload = json.loads(sys.stdin.read() or "{}")
        except json.JSONDecodeError:
            payload = {}
        url = payload.get("url") or (payload.get("urls") or [None])[0]
        if not url:
            print("{}")
            return 1
        try:
            print(json.dumps(scrape_group(fetch(url), url)))
        except (urllib.error.URLError, OSError) as exc:
            print(f"fetch failed: {exc}", file=sys.stderr)
            print("{}")
            return 1
        return 0

    if "--build-series-cache" in sys.argv:
        index = build_series_index(lambda m: print(m, file=sys.stderr))
        path = cache_path()
        json.dump({"fetched": time.time(), "index": index}, open(path, "w", encoding="utf-8"))
        print(f"{len(index)} files mapped -> {path}", file=sys.stderr)
        return 0
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        payload = {}
    # sceneByURL sends {"url": ...}; sceneByQueryFragment sends the chosen search result
    # back, which is one of the objects sceneByName returned, so it carries the url too.
    url = payload.get("url") or (payload.get("urls") or [None])[0]
    if not url:
        print("no url supplied", file=sys.stderr)
        print("{}")
        return 1
    try:
        markup = fetch(url)
    except (urllib.error.URLError, OSError) as exc:
        print(f"fetch failed: {exc}", file=sys.stderr)
        print("{}")
        return 1
    print(json.dumps(scrape(markup, url)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
