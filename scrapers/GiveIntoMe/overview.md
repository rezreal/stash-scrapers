# Give Into Me

`sceneByURL` and search for `https://giveinto.me/file/<variant_id>` (Earesistible).
Python, standard library only, with a cached catalogue.

| Field | Source |
| --- | --- |
| Title | `title`, plus the variant's own title when it has one |
| Date | the variant's `date` |
| Duration | the variant's `duration`, parsed to seconds |
| Details | synopsis, description, then labelled Instructions / Intended Effect / Notes |
| Image | `image` |
| Code | the file `_id` |
| Performer / Studio | fixed `Earesistible` |
| Tags | variant tags, plus `Type:`, audience, `Tone:` and `Tier:` |

## Why this one is a script, not XPath

There is no HTML to select against. The site is a Next.js **static export**: `/files` and
`/file/<id>` both ship as a ~2.6 KB shell with `pageProps: {}`, no `<title>`, no og tags,
no meta description. I probed a nonsense slug and got a 200 with an identical shell. There
is no sitemap either.

So the metadata only exists in the site's JSON API:

```
GET /api/get/<variant_id>        one file, with every variant
GET /api/list/full?getFilters=1  the entire catalogue — 183 items in one response
```

**`/api/` is Disallowed in the site's robots.txt.** This scraper uses it anyway, at the
owner's instruction. It is kept to the minimum that works: one request per scrape, and the
catalogue cached for six hours rather than re-fetched per lookup. If you would rather not,
there is no fallback — the data is not available anywhere else.

## The URL takes a variant id, not a title slug

Easy to get wrong. Every `image` URL contains a title slug —
`free.giveinto.me/conditioned-for-slumber/image.webp` — and the title slugifies to it on
all 183 files with zero mismatches, which makes it look like the route key. It isn't. The
frontend builds links as:

```js
href={`/file/${e.first_variant_id}`}
```

So the route is `/file/e555-32fb-a6af-464f`. Because pages are client-rendered shells, a
wrong slug still returns 200, so this would not have failed loudly.

A file's other variants have their own ids and their own URLs. The scraper uses whichever
variant the URL names, falling back to the first.

## Search is nearly free

The whole catalogue is a single response, so `sceneByName` searches a cached copy rather
than querying per keystroke. Filenames work directly — an `[F4M]` prefix and an audio
extension are stripped before matching, and results are ordered by closeness to the title.

## Tiers and the `[UPGRADE]` sentinel

Fields your tier cannot reach come back as the literal string `[UPGRADE]` rather than a
URL or an error, and each asset has its own tier: `tier_listen`,
`tier_haptics_funscript`, `tier_haptics_audio`, `tier_lossless`. So one file can have a
free Opus and a free funscript while its MP3 and lossless need a subscription.

The tier names come from the site's own bundle:

| id | tier |
| --- | --- |
| -1 | Free |
| 7841239 | Good Pet |
| 7841240 | Loving Pet |
| 7841241 | Submissive Pet |
| 7841330 | Dedicated Pet |
| 7841449 | Devoted Hypnoslut |

91 of the 183 files are Free.

## Durations come in four shapes

`7m 27s`, `1h 2m 3s`, `45s`, and one record with a double space (`19m  0s`), so they are
parsed by unit rather than by position.
