# JOI-Me

`sceneByURL` scraper for `https://joi-me.com/<category>/<id>-<slug>.html`. Pure XPath, no
script, no cache.

| Field | Source |
| --- | --- |
| Title | `og:title` with the performer prefix stripped |
| URL | `og:url` |
| Details | `<meta name="description">` |
| Image | `og:image` |
| Performer | the first `/tags/` link |
| Tags | every other `/tags/` link |

Verified against 45 article pages sampled from `/lastnews/` and three tag listings: every
field resolves on all 45, exactly one performer each, and the performer never leaks into
the tags or the title.

## Splitting the performer from the title

`og:title` is `Performer - Title` on all 45 pages, and the site offers no structural way to
tell the performer from the tags — every `/tags/` link sits in one flat list of `<span>`s.
Two independent signals agree, which is what makes the split trustworthy:

* the **first** `/tags/` link is the performer
* `og:title` **starts with** that same name — checked on all 45, matched every time

So `Title` strips with a non-greedy `^.*? - `. That matters because 4 of the 45 titles
contain a *second* `" - "`, which a greedy match would eat; and since no performer name in
the sample contains `" - "`, the regex gives the same answer as stripping the tag by value,
without needing to compute anything.

The parentheses in the performer and tag selectors are load-bearing:
`(//a[...])[1]` applies `position()` to the whole node-set, whereas `//a[...][1]` would
apply it per parent element and match far more than one node.

## Deliberately not mapped

* **`<meta name="keywords">`** — DataLife Engine generates it from the description, so it
  is prose words (`loose, dangerous, guilty, clear, becomes`), not tags, and the first is
  not the performer. Mapping it would put junk on every scene.
* **Date** — the article's own `li.meta_date` is when the entry was posted to this index,
  not when the clip was released, so it would be wrong on the scene. The page's other
  `<time>` elements belong to the sidebar's related items rather than to the article at
  all.
* **Studio** — the site is an index, not the producer.
* **Download links** — out of scope for this scraper.
