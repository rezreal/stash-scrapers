# Shibbydex

Two pieces, same split as the Mistress Calia scraper: Stash's scraper interface returns
**metadata only**, so downloads live in a separate CLI.

| File | What it does |
| --- | --- |
| `Shibbydex.yml` + `Shibbydex.py` | `sceneByURL` scraper for `https://shibbydex.com/file/<uuid>` |
| `shibbydex-dl.py` | Standalone CLI: audio + haptics + cover for **Free-tier** files |

## 1. The scraper

```bash
cp Shibbydex.yml Shibbydex.py ~/.stash/scrapers/
```

Then **Settings → Metadata Providers → Scrapers → Reload scrapers**. Both files go in the
same directory; the script needs `python3` on PATH and nothing else — standard library
only, no `py_common`, no `lxml`.

| Field | Source |
| --- | --- |
| Title | the `<h1>` in the detail column (`<title>` appends ` - Shibbydex`) |
| Date | `Release Date`, rewritten from `DD/MM/YYYY` |
| Code | the file UUID |
| Duration | from the player payload (unlocked files only) |
| Details | the description, labelled Credits / Notes / … blocks, and the table's prose fields |
| Image | cover art **only when real** |
| Performer | the `Artist` field, split on commas |
| Studio | fixed `Shibby` |
| Tags | tag badges + `Audience` + prefixed triggers + the table's label fields |
| Groups | the site's series, via a cached reverse index |

Verified against 100 file pages sampled across all 32 listing pages (2,041 files): every
field resolves on all 100, free and Patreon-locked alike.

### Searching by filename

For scenes whose URL you do not have, **Scrape with → Shibbydex → search**. Stash sends
the scene's title (or whatever you type), the scraper searches the site, and you pick from
the results; picking one then fetches the full metadata.

Filenames are handled directly, so a file the downloader saved under the site's own name
works without editing. The audience prefix and the trailing bracket run are stripped
before searching:

```
[F4M] - Alpine Spa & Massage Retreat - [Script Fill];[Massage][Anal].mp3
        -> searches for "Alpine Spa & Massage Retreat"
```

`/search?search=X` just redirects to `/files?search=X`, which renders the same file cards
as the listing, so the results carry title, date, description and cover art.

The site's search also matches descriptions, so an exact title can come back below a
weaker hit — results are therefore reordered by how closely the title matches before Stash
shows them. Tested against downloaded filenames, the exact title is the top result 6 times
in 7; the miss was a file whose on-site title genuinely differs (`Endless Edging Part 1`
is listed as `Endless Edging`).

Two config details worth knowing if you edit the YAML: `sceneByName` and
`sceneByQueryFragment` are **single objects, not lists** (unlike `sceneByURL`), and Stash
only offers the search option when **both** are defined.

### Trigger tags

The Triggers section groups badges under `Installed` and `Used` sub-headings, so they come
out prefixed:

| group | tag |
| --- | --- |
| Used | `Trigger: Good Boy` |
| Installed | `Trigger Installed: ⋇ : Snaps` |

87 of the 100 sampled pages have a Triggers section — 845 `Used` tags and 22 `Installed`
across them. A trigger can be in both groups (all 15 pages with an `Installed` group have
at least one), which produces both tags. That is deliberate: installing and using a
trigger are different facts about the file.

**This is why the scraper is a script.** A mapped XPath field has exactly one selector and
one `postProcess` chain, so `Tags` cannot emit one prefix for one set of badges and a
different prefix for another. No amount of XPath gets around it.

### The 2026 player rewrite

The site replaced its `<audio>` element with a JS player. Everything that used to sit in
`data-` attributes now lives in a `<script type="application/json" id="sd-player-data">`
blob, and that blob is only emitted for a file the current session may actually play — so
locked files and video embeds have none.

It is better structured than what it replaced, and gives the scraper a field it never had:
`duration`, in seconds. The file page states no runtime anywhere else.

Two things moved with it:

* haptic kinds were renamed — `funscript` is now `vibe`, `buttplug` is now `plug`,
  `stroker` unchanged. The old endpoint names still redirect, so nothing broke immediately.
* sibling variations moved from `a.btn-sm` to `a.sd-variant-card`. Both are matched.

### The details table

Being a script buys **no new fields** — the wire format is the same `ScrapedScene` type an
XPath scraper produces (`title, code, details, director, url(s), date, image, studio, tags,
performers, groups, duration, remote_site_id, fingerprints`), and Stash has no custom
fields on scenes. What it buys is being able to route each table row to the field that
suits it, which a single XPath selector cannot do.

The split follows how the values actually behave across the 100 sampled files:

**Label-like → prefixed tags.** `Tier` (4 distinct values), `Level` (4), `Aftercare` (3),
`Wakener` (4), `Background` (6), `Consent` (14), `DS` (15), `Effects` (23), `Orgasm` (31),
`Setting` (36), `Author` (36), `Tone` (41). Comma-separated cells are split, so
`Setting: Modern, Dungeon` becomes two tags. The prefix matters — bare `None`, `Yes` and
`Modern` would collide with the file's own tag badges.

```
Tier: Free   D/s: Female Domination   Tone: Dominant   Setting: Dungeon   Author: Broken
```

**Prose → details.** `Induction`, `Deepener`, `Body`, `Instructions` and `Intended Effect`
average 150–230 characters. They are appended as labelled blocks, same as Credits.

**Skipped.** `Release Date` and `Audience` are already fields, `Play Count` changes on
every view, `Genitalia` was `N/A` on all 100 files, and `Script` / `Reddit` /
`Haptic Script` are cross-references to other files rather than facts about this one.

Values that mean "nothing here" (`N/A`, `None`, `-`) are dropped — that filter applies to
the performer too, since some files list the Artist as `N/A` and would otherwise create a
performer by that name. `No` is kept, being a real answer to *Aftercare*.

Commas inside parentheses do not split: `Vanilla (Shibby, Good Boy)` is one value, and
splitting it naively produced the tags `D/s: Vanilla (Shibby` and `D/s: Good Boy)`. Cells longer than 40 characters are treated as prose and left
out of the tags; one file answers `Orgasm` with a whole clause.

The result is a median of 50 tags per scene. All of this lives in two tuples at the top of
`Shibbydex.py` — `TABLE_TAGS` and `TABLE_DETAILS` — so trimming it is a one-line edit.

There is one field the site cannot fill: `ScrapedScene.duration` exists, but no file page
states a runtime anywhere.

### Description

The description is `<p class="lead">`, with its line structure carried by `<br>` tags — the
script renders those as real newlines and keeps inline `<a>`/`<i>`/`<span>` inline.

Pages carry up to four *more* `p.lead` blocks under `Credits`, `Notes`, `Disclaimer`,
`Haptics` or `Warning` headings. These are per-file text, not boilerplate (Credits is 41
distinct across 46 occurrences, Notes 32 of 38, Haptics 11 of 11), so they are kept and
labelled:

```
You think cause I'm a girl I can't beat your ass?

Ready to wrestle?

Credits: This script was written by u/ALittleBitBritish.
```

Stash renders `details` as plain text (`<p className="pre">`), so newlines and blank lines
survive but markdown does not — hence `Credits: ` rather than `**Credits**`.

### Cover art

Real cover art when the file has any, on `assets.shibbydex.com`. Only 6 of 100 sampled
files do; the rest fall back to a shared logo, and video files have no `<img>` at all and
so return nothing, leaving whatever cover the scene already has.

The fallbacks are not really per-audience — **three** exist (`thumbnail_logo_f4a`, `_f4m`,
`_f4f`) and everything else reuses the f4a one, F4NB and F4TM included.

They need no converting, for two reasons worth knowing:

* **Stash decodes webp.** `pkg/image/webp.go` is a real decode path, and `golang.org/x/image`
  is a direct dependency — a webp cover is not a second-class citizen.
* **Identical bytes cost one blob.** A scraped image URL is fetched by Stash and stored as
  a data URI in a checksum-keyed store (`blobRow{Checksum, Blob}` in `pkg/sqlite/blob.go`),
  so however many scenes share a placeholder, the three logos occupy three rows. Verified —
  the three files hash to `d16b7d5a…`, `272efd1a…` and `cf6e0f77…`.

Converting to PNG would in fact make this *worse*: webp cannot be decoded from the Python
standard library, so it would mean a Pillow dependency on a scraper that currently needs
nothing, in exchange for larger images and no behavioural gain. The deduplication you were
after already happens, and is deterministic because the logos are static files.

Set `PLACEHOLDER_ART = False` at the top of `Shibbydex.py` for real art only.

### Patreon-locked files and your session

`Tier` is `Free` on 855 of the 955 indexed works; the rest are `Hypnosub` (46),
`Hypnoslave` (44) and `Devoted Pet` (8), with 2 unlabelled. Locked pages still carry the **full**
title, description, date, tags and details table — only the player and download button are
replaced by a *Connect to Patreon* link. So scraping needs no login at all; only
downloading is gated.

If you have a subscription, both scripts will use your session. The site authenticates
with a Laravel cookie (`__Secure-shibbydex_session`), so no password is involved:

1. Log in at shibbydex.com in your browser.
2. Export `cookies.txt` (Netscape format) with any cookie-export extension.
3. Save it as `shibbydex-cookies.txt` next to the scripts.

#### What the file looks like

Simplest form — one `name: value` (or `name=value`) per line, or a whole `Cookie:` header.
Only `__Secure-shibbydex_session` matters:

```
__Secure-shibbydex_session: eyJpdiI6…
```

Netscape cookies.txt also works, which is what browser extensions export: one cookie per
line, **seven tab-separated fields** — domain, include-subdomains, path, secure, expiry
(unix seconds), name, value.

```
# Netscape HTTP Cookie File
shibbydex.com	FALSE	/	TRUE	1800000000	__Secure-shibbydex_session	eyJpdiI6…
shibbydex.com	FALSE	/	FALSE	1800000000	XSRF-TOKEN	eyJpdiI6…
```

`__Secure-shibbydex_session` is the one that matters; `XSRF-TOKEN` is only needed if you
ever POST. Both values are long base64 blobs — paste them whole.

**Copy the whole value.** Session values are long and percent-encoded, usually ending
`%3D`. Losing the last character leaves a broken `%3` escape, the site silently treats you
as anonymous, and everything above Free looks locked. If `data-user-tier` reads 0 with
cookies loaded, check the tail of the value first.

Anything a browser extension exports works as-is. If you hand-write it, note that the
loader here is deliberately lenient, because Python's own `MozillaCookieJar` is not:

* the `# Netscape HTTP Cookie File` header is optional here (`MozillaCookieJar` rejects a
  file without it)
* the leading dot and the second column need not agree — `shibbydex.com FALSE` and
  `.shibbydex.com TRUE` both work (`MozillaCookieJar` asserts on the mismatch, which is
  the usual reason a hand-written file fails)
* the `#HttpOnly_` prefix browsers put on session cookies is stripped
* spaces are accepted instead of tabs, as long as no value contains one

Cookies go into a scoped jar rather than a raw header, so they are sent to shibbydex.com
and **not** forwarded to the Cloudflare R2 host the download redirects to. Verified.

Alternatively put the raw `Cookie:` header value in `$SHIBBYDEX_COOKIE`, which takes
precedence — though a scraper inherits Stash's environment, so the file is usually easier
there. The downloader also takes `--cookies PATH` and prints which source it used.

With a session loaded the downloader stops deciding from `Tier` and asks whether the page
offers *this* session a `/download/<uuid>` link. That positive check matters, because there
are two different lock messages and matching on either one is fragile:

| state | message |
| --- | --- |
| logged out | *Connect to Patreon to unlock* |
| logged in, below the required tier | *Upgrade tier to unlock (Devoted Pet)* |

The message is echoed when a file is skipped, so you can see which case you hit. Seeing
*Connect to Patreon to unlock* while logged in means the cookies did not load at all —
that is the logged-out message, so the request went out anonymously.

If the cookie line reads `cookies: $SHIBBYDEX_COOKIE`, an environment variable is in use
and the file is being ignored entirely — that override is silent apart from that one line.

The downloader also reports the site's `data-user-tier` (0 when anonymous) the first time it
changes — the quickest way to confirm the cookies took.

Verified against a real subscription at `data-user-tier=3`: `Free`, `Hypnosub` and
`Hypnoslave` files all download, and `Devoted Pet` correctly reports *Upgrade tier to
unlock*. Tier numbers are not a simple ordering of the tier names, so treat the download
link as the authority rather than the label.

### Variations

A file can have sibling variations — audience cuts (F4A / F4M / F4F / F4TF / F4S), `Naked
Audio`, `Video`. They are separate UUIDs with **identical titles**. Of the sampled files 22
had none and one had 21.

Because the titles collide, `Audience` is scraped as a tag — otherwise nothing in Stash
would tell two variations apart.

Variations do **not** form a group — they are the same work in different cuts, and the
`Audience` tag is what tells them apart. Groups are reserved for the site's series.

### Series → Groups

The 32 entries on [/series](https://shibbydex.com/series) become Stash Groups, and a scene
in one gets `groups: [{name, urls, synopsis, studio}]`. Files in two series get two groups.

A file page does **not** say which series it belongs to. The only `/serie/` links that ever
appear on one are prose hyperlinks the author happened to write into a paragraph — 6 of the
100 sampled pages have one, and on the file I checked it sat mid-sentence under a *Haptics*
heading rather than in any field. The mapping only exists in the other direction, so the
scraper builds a reverse index from `/series` plus each `/serie/<uuid>` page and caches it
in `shibbydex-series.json` next to the script (or the temp dir, if the scrapers directory
is not writable).

* 32 series, no pagination on the index or on any series page
* 323 of the 2,041 files are in a series — 16%; 9 of those are in two
* series sizes run 1 to 48 files
* 33 sequential requests, about 16 seconds, refreshed every 7 days

The first scrape after installing pays that 16 seconds; later ones read the cache. To pay
it up front instead:

```bash
python3 Shibbydex.py --build-series-cache
```

If the index cannot be built the scrape still succeeds, just without a group.
