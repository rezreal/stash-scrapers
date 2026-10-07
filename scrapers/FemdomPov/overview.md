# Femdom POV

`sceneByURL` scraper for `https://femdom-pov.me/<slug>/`. Pure XPath, no script, no cache.

| Field | Source |
| --- | --- |
| Title | `og:title`, site suffix and performer prefix stripped |
| URL | `og:url` |
| Details | `og:description` |
| Image | `og:image` |
| Performer | the `og:title` prefix — blank when the title has no separator |
| Tags | `rel="tag"` links |

Verified against 40 post pages sampled from four categories: every field resolves on all 40.

## Three traps on this site

**The tag cloud.** Every post carries a sidebar of ~45 `/tag/` links that is byte-identical
across pages. A plain `//a[contains(@href,"/tag/")]` would tag every scene with the entire
cloud. WordPress's own `rel="tag"` markup is what distinguishes the post's real tags — 1 to
13 per post, median 5.

**The title suffix varies.** 37 of 40 sampled posts end in `| Femdom POV`, the other 3 in
the longer site title, so the suffix is stripped from the *last* `|` rather than matched
literally.

**Not every post is `Performer - Title`.** Site announcements (`Happy New Year!`) and some
oddly-punctuated titles have no separator — 5 of 340 across one whole category. Left alone,
the extract would pass those through unchanged and invent a performer called
`Happy New Year!`. So the postProcess marks titles that *do* contain a separator, blanks
the ones that don't, and only then takes the prefix. Better an empty performer than a
fictional one.

One of those unmatched titles is `Jerk To My Feet – Victoria Vargaz – Naughty and Nice`,
which uses en-dashes and puts the performer in the *middle*. Splitting on en-dashes as well
would extract `Jerk To My Feet` as the performer, so the separator set is deliberately
limited to the ASCII hyphen.

## Weaker than the JoiMe scraper in one respect

There is no performer taxonomy here to cross-check against. On joi-me.com two independent
signals agree — the first tag link and the title prefix. Here `rel="tag"` is genres
(`Femdom Pov`, `JOI`, `Hypno`) and matched the performer on 1 of 40 posts, so the title
prefix is the only source. A post titled in some other shape will simply get no performer.

## Not mapped

* **Studio** — the site is an index, not the producer.
* **Date** — `article:published_time` is on every post, but it is when the entry was added
  here, not the clip's release. The catalogue tool records it as `posted`; the scraper
  leaves the scene date alone.
* **Download links** — out of scope.
