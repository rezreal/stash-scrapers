# Mistress Calia

Two pieces, because Stash's scraper interface returns **metadata only** — it has no
field for a media file or a funscript, and no way to write files to disk.

| File | What it does |
| --- | --- |
| `MistressCalia.yml` | `sceneByURL` XPath scraper: title, date, description, cover, performer (+ studio, tags) |
| `mistresscalia-dl.py` | Standalone CLI: downloads the audio + funscript + cover for **free** posts |

## 1. The scraper

Install:

```bash
cp MistressCalia.yml ~/.stash/scrapers/
```

Then **Settings → Metadata Providers → Scrapers → Reload scrapers**. On any scene,
paste a `https://mistresscalia.com/<slug>/` URL into the URL field and hit the
scrape button (or use *Scrape With → Mistress Calia*).

Stash matches scraper URLs by substring, not glob, so `mistresscalia.com/` in the
`url:` list is the equivalent of `https://mistresscalia.com/*/`.

### Where each field comes from

| Field | Source |
| --- | --- |
| Title | `<meta property="og:title">` (the bare title; `<title>` has ` - Mistress Calia` appended) |
| Date | `<meta property="article:published_time">` (UTC), truncated to `YYYY-MM-DD` |
| Details | the `og:description` teaser, then every non-empty `p`/`h2`/`h3`/`h4`/`li` under `.entry-content`, blank-line separated |
| Image | `<meta property="og:image">`, falling back to `twitter:image` — the full-size original, not a resized variant |
| Performer | fixed `Mistress Calia` |
| Studio | fixed `Mistress Calia` |
| Tags | the post's WordPress tags + categories |

Four things make the Details selector fussier than it looks:

* **The teaser lives in `<head>`.** `og:description` is a WordPress excerpt. 295 of the
  362 posts carry it *only* as a meta tag; 90 also render it into `div.acp-post-audio`.
  Prepending the meta gives every post the same opening line, and
  `not(normalize-space() = //meta[...])` drops the rendered copy on the 65 posts where
  the two are byte-identical, so nothing repeats. If your Stash build rejects that
  node-set comparison, delete the clause — you get 65 duplicated lines back and
  everything else keeps working.
* **Descendant axis, not child.** Besides the teaser, some posts lay text out in
  `wp-block-columns`. A child-only `/p` silently drops both.
* **Boilerplate is filtered by content, not by position.** Cutting at the first `<h3>`
  would be simpler but wrong — `break-hands-free-orgasm` and `femdom-obsession-part-1`
  carry real body copy under their own subheadings. So the *Haptics* blurb, the
  *More Erotic Hypnosis* related-posts block and the player's own chrome (`0:00`,
  "Your browser does not support the audio element") are each excluded by name.
* **Collapsed `<details>` accordions are skipped.** 19 posts hide the audio's full
  transcript behind a *Read the Script* expander inside `.entry-content`. Without
  `not(ancestor::details)`, `the-edge-of-sanity` scrapes a 111,617-character Details
  field instead of its 1,702-character description.

Two things you may want to change:

* **Drop the tags** — delete the `Tags:` block. Categories bring in `Audio`,
  `Free Files` / `Paid Files`, `Hypnosis`, `Roleplay`; post tags bring in kinks
  plus useful ones like `Duration: 10-20` and `Haptics`.
* **Teaser only** — replace the whole `Details` selector with
  `//meta[@property="og:description"]/@content` if you want just the one-line summary
  and none of the body copy.

Paragraph separation is preserved: `concat: "\n\n"` puts a blank line between blocks,
and Stash only collapses whitespace *within* each node, not the separator.

Verified against **all 362 posts** in `post-sitemap.xml`, free and paywalled: every
field resolves on every one — no missing title/date/image/tags, no empty Details, the
teaser first on all 362 and duplicated on none, and no boilerplate, player chrome or
transcript leaking through. Median Details is ~574 characters; the three outliers above
30k are the short-story posts, whose body genuinely is that long.
