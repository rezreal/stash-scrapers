# Downloaders

Companion CLIs. Stash's scraper interface returns metadata only — it cannot write files —
so anything that fetches audio, funscripts or covers lives here rather than in a scraper.

Both are standard library only, no pip install.

## mistresscalia-dl.py


Free posts expose a direct `wp-content/uploads/*.mp3` link; paid posts only expose a
short sample under `/audioprotectsample/`, so those are detected and skipped.

```bash
./mistresscalia-dl.py https://mistresscalia.com/teased-into-trance-at-the-game-store/ -o ~/stash/calia
```

```
teased-into-trance-at-the-game-store.mp3
teased-into-trance-at-the-game-store.funscript
```

Stdlib only — no pip install.

```
-o, --outdir DIR   target directory (default: .)
    --name         basename from URL slug (default) or post `title`
    --cover        also save the cover image
    --no-funscript skip the funscript
-f, --force        re-download files that already exist
-n, --dry-run      print what would be downloaded
```

### Why the shared basename

Stash flags a scene as interactive when a `.funscript` sits next to the media file
with the **same basename**. On the site the two rarely match — the game-store post
serves `ITSTIMETODDDDUEL.mp3` alongside
`Teased-into-Trance-at-the-Game-Store.funscript` — so the script renames both to the
post slug. Scan the folder afterwards and the scene comes in interactive, ready for
the scraper to fill in.

Exit status is non-zero if any URL was skipped or failed.

## joime-catalogue.py

Builds a CSV catalogue of joi-me.com clips matching a filter. Metadata only — the site's
download links are not touched.

```bash
./joime-catalogue.py --tag stroking -o stroking.csv
./joime-catalogue.py --search "verbal humiliation" --pages 5 -o humiliation.csv
./joime-catalogue.py --url https://joi-me.com/lastnews/ --fast -o recent.csv
```

Columns: `url, post_id, category, performer, title, tags, description, image, posted`.
Tags are pipe-separated, and the performer is split out of both the tag list and the title
by the same rule the JoiMe scraper uses.

```
--tag / --search / --url   pick one; --url takes any listing page
--pages N                  stop after N listing pages
--limit N                  stop after N clips
--fast                     listing pages only — skips the description, ~18x fewer requests
--append                   continue an existing CSV, skipping URLs already in it
-j N                       parallel requests (default 1 — see below)
-o PATH                    output (default: joime.csv)
```

Listing pages carry everything except the description, so by default each clip's page is
fetched to add it — one request per clip. `--fast` works from the listings alone: 18 clips
per request instead of 18 requests. Both stop early when a page returns nothing new.

`posted` is the date the entry appeared on the index, not the clip's release date.

**Rows are written as they are gathered.** The CSV grows page by page and is flushed after
every row, so `tail -f` works and a run stopped with Ctrl-C — or killed — still leaves a
complete, valid file of everything collected up to that point. Verified by killing a run
mid-crawl: 108 whole rows, header and columns intact.

**Don't raise `-j`.** Cloudflare sits in front of the site and rate-limits bursts: six
parallel requests earned `429` on every one of them, and the penalty then applied to
*serial* requests for about twenty seconds afterwards. The default is therefore 1, with
retries backing off 5s / 15s / 30s on a 429. Serial with backoff is what actually finishes;
concurrency just trades throughput for a ban.

**A whole tag is bigger than it looks.** `stroking` runs to page 2385 — the last page
holds 11 clips and page 2400 returns none — so roughly **42,900 clips**. That is about
2,400 requests in `--fast` mode and about 43,000 with descriptions. Use `--pages` or
`--limit`, or start with `--fast` and enrich later with `--append`.

**This one needs `curl`.** joi-me.com serves only over HTTP/2 and answers 403 to every
HTTP/1.1 request whatever the headers say — verified, `curl --http2` returns 200 and
`curl --http1.1` returns 403 for the same URL. Python has no HTTP/2 client in the standard
library, so this tool shells out to curl rather than using `urllib` like the other two.

## femdompov-catalogue.py

CSV catalogue of femdom-pov.me clips. Same idea as `joime-catalogue.py`, kept as a separate
program because the two sites share nothing but the need for HTTP/2 — this one is
WordPress, that one DataLife Engine.

```bash
./femdompov-catalogue.py --tag hypno -o hypno.csv
./femdompov-catalogue.py --category femdom-audio --pages 5 -o audio.csv
./femdompov-catalogue.py --url https://femdom-pov.me/latest-news/ --fast -o recent.csv
```

Columns: `url, slug, source, performer, title, tags, description, image, posted`.

```
--tag / --category / --url   pick one
--pages N / --limit N        caps
--fast                       listing pages only — no tags, description or date
--append                     continue an existing CSV
-o PATH                      output (default: femdompov.csv)
```

Differences from the joi-me tool worth knowing:

* **No `--search`.** The site's search is disabled — `/?s=term` 302s to the homepage.
* **`--fast` gives less here.** Listing cards carry only URL, title and cover, so tags,
  description and date all need the post page. On joi-me the cards carry the tags too.
* **End of listing is a 404**, not an empty page, so that is what stops the crawl. A whole
  category run ended cleanly at page 20 of `new-year` with 340 clips.
* **No parallelism**, same as the other tool: Cloudflare fronts this site too, and 429s are
  retried with 5s/15s/30s backoff.

`performer` is blank when a title has no ` - ` separator — 5 of 340 in one category, which
is correct rather than a failure. See the scraper's overview for why en-dashes are not
treated as separators.

## shibbydex-dl.py


```bash
./shibbydex-dl.py https://shibbydex.com/file/<uuid> -o ~/stash/shibby --variations
```

Everything is saved under one shared basename, which is what Stash keys on when it flags a
scene interactive. By default that basename is the site's own filename, lifted from the
`Content-Disposition` of the download redirect:

```
[F4M] - Alpine Spa & Massage Retreat - [Script Fill][Commissioned];[Massage][Anal].mp3
[F4M] - Alpine Spa & Massage Retreat - [Script Fill][Commissioned];[Massage][Anal].funscript
```

The download filename and the scraped title are independent on purpose: the file on disk
keeps the site's name, while Stash gets the title from the page. They sometimes differ —
`9e6ad9c5…` is titled *Mindless for Nylons* on the page but downloads as
`Mindless for Stockings.mp3`. Use `--name title` if you would rather they match.

Characters illegal on Windows/SMB (`<>:"/\|?*`) are replaced and the stem is capped at 180
characters; `[`, `]`, `;` and `&` are kept as the site writes them. Most site filenames
start with the audience, which keeps variations apart, but not all do — when two files in
one run would claim the same basename, the audience is appended to the second rather than
overwriting the first.

Stdlib only — no pip install.

```
-o, --outdir DIR    target directory (default: .)
    --name          basename from the site's own filename (default), the title, or uuid
    --variations    also fetch the file's sibling variations
    --cover         also save real cover art, when the file has any
    --all-haptics   save every haptic kind, not just the funscript
    --no-haptics    skip haptic files entirely
-f, --force         re-download existing files
-n, --dry-run       print what would be downloaded
```

### Haptics

Haptic files take the audio's basename with the kind folded into the extension, so the one
Stash pairs with the media keeps the bare suffix:

| kind | filename |
| --- | --- |
| vibe (was funscript) | `<name>.funscript` |
| plug (was buttplug) | `<name>.plug.funscript` |
| stroker | `<name>.stroker.funscript` |

The kinds are **not hardcoded**. Since the 2026 player rewrite they come from the
`<script type="application/json" id="sd-player-data">` payload's `file.haptics` map, so a
new kind is picked up without a code change; the old `<audio data-*>` markup is still read
as a fallback. That payload also carries the tier and duration, and is absent entirely for
files the session cannot play. Guessing at endpoint names is unreliable: `/haptic/<uuid>/stroker`
returns 404 on a file that has no stroker script and 302 on one that does.

Only the plain `.funscript` is fetched by default; `--all-haptics` gets the rest.

Whether the kinds differ varies by file. On *Alpine Spa & Massage Retreat* all four names
— `vibe`, `plug`, and the legacy `funscript`/`buttplug` — return byte-identical JSON. On
*Descending Shibby's Staircase* they are genuinely different patterns: 4554 actions for
`vibe`, 3761 for `plug`, 1515 for `stroker`. So `--all-haptics` is worth using if you drive
more than one kind of device.

### Duplicate variants

The site stores every variant as its own object even when the audio is byte-identical.
Three variants of *The Nymphs of Latex Lake* — the plain one, `[Naked]` and
`[No Binaurals]` — are all exactly 88,501,919 bytes with the same content; four variants of
*Her Wetness, Your Surrender* likewise. Nothing on the site marks them as the same file.

So before downloading, each file is fingerprinted with one ranged request — its true size
plus an MD5 of a 256 KB slice — and a variant matching one already fetched in this run is
skipped:

```
The Nymphs of Latex Lake [F4A]     → The Nymphs of Latex Lake.mp3
The Nymphs of Latex Lake [N/A]     = identical audio to The Nymphs of Latex Lake — skipped
The Nymphs of Latex Lake [F4A]     = identical audio to The Nymphs of Latex Lake — skipped
```

Real alternate cuts are unaffected — *Descending Shibby's Staircase* and its
`[Bonus Deepener]` are 118 MB and 27 MB, and both download. `--keep-duplicates` disables
the check. If the fingerprint request fails the file is downloaded normally; an unknown
fingerprint is never treated as a match.

### Video files

13 of the 100 sampled files are an embedded video rather than an audio file — `/play` and
`/download` both answer 404 for them. They are skipped, and `--variations` will not walk
into a `Video (…)` sibling either. Audio only.

### The endpoints

All are plain URLs keyed on the file UUID, and all work anonymously for Free-tier files.
Each 302s to a signed Cloudflare R2 URL.

| Endpoint | Returns |
| --- | --- |
| `/play/<uuid>` | the audio stream |
| `/download/<uuid>` | the same audio, with the site's filename in `Content-Disposition` |
| `/haptic/<uuid>/<kind>` | haptic JSON for that kind |

Above the Free tier, `/play` and `/download` answer **403** and `/haptic` answers **404**,
so those files are detected from `Tier` and skipped rather than half-downloaded.

### The file index

Selecting what to download by hand across 955 works is tedious, so the downloader keeps a
local index of the `/files` listing:

```bash
./shibbydex-dl.py --build-index      # full crawl, 32 pages, ~22s
./shibbydex-dl.py --update-index     # newest first, stops at the first fully-known page
```

The listing shows one card per work with its variations as pills, so the index holds **955
entries covering 2,041 files** — verified against a raw scrape of the same pages, 0 missing
and 0 extra. Each entry carries title, description, date, tier, audience, play count,
duration and its variation UUIDs. Duration is worth noting: it appears only in the listing,
never on a file page.

Then download from it, with filters:

```bash
./shibbydex-dl.py --from-index --tier Free --audience F4M --since 2026-01-01 --list
./shibbydex-dl.py --from-index --match "hypno.*staircase" --variations -o ~/stash/shibby
```

```
--tier / --audience    exact match on those fields
--match REGEX          title or description
--since YYYY-MM-DD     release date
--limit N              stop after N
--list                 print the selection instead of downloading
--index PATH           index location (default: shibbydex-index.json beside the script)
```

The index picks *what* to fetch; each file page is still loaded before downloading, since
the haptic kinds and the real cover only appear there. Filtering on tier first does save
fetching pages you cannot download.

### Rate limiting

The site rejects concurrent requests — fetching 100 pages with 4 workers failed 57 of
them, and the same 100 sequentially with a 0.4s delay failed none. The downloader is
deliberately sequential for that reason.
