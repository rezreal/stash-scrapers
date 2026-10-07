# Stash scrapers

Scrapers for a few adult sites, plus the download tools that go with two of them.

| Scraper | Site | Type |
| --- | --- | --- |
| [MistressCalia](scrapers/MistressCalia) | mistresscalia.com | XPath, `sceneByURL` |
| [Shibbydex](scrapers/Shibbydex) | shibbydex.com | Python, `sceneByURL` + search + `groupByURL` |
| [JoiMe](scrapers/JoiMe) | joi-me.com | XPath, `sceneByURL` |
| [FemdomPov](scrapers/FemdomPov) | femdom-pov.me | XPath, `sceneByURL` |

Each scraper has an `overview.md` with what it maps, what it deliberately skips, and how
it was verified. The [downloaders](tools) live separately, because Stash's scraper
interface returns metadata only and cannot write files.

## Install

Stash walks its scrapers directory recursively and follows symlinks
(`fsutil.SymWalk` in `pkg/scraper/cache.go`, loading every `.yml` it finds), so the
simplest thing works — point one symlink at this repo:

```bash
git clone <your-repo-url> ~/src/stash-scrapers
ln -s ~/src/stash-scrapers/scrapers ~/.stash/scrapers/mine
```

Then **Settings → Metadata Providers → Scrapers → Reload scrapers**. Both scrapers appear,
and `git pull` + reload updates them. Verified: one symlink, both `.yml` files discovered
at depth two, with `Shibbydex.py` resolving next to its `.yml` — which matters, because
Stash runs a script scraper with its working directory set to the `.yml`'s own directory.

Copying instead of symlinking works just as well:

```bash
cp -r scrapers/Shibbydex ~/.stash/scrapers/
```

Shibbydex needs `python3` on PATH. Neither scraper needs any pip package — both are
standard library only.

**Copy the whole directory, not just the `.yml`.** Stash runs a script scraper with its
working directory set to the `.yml`'s own directory, so `Shibbydex.py` has to sit beside
`Shibbydex.yml`. A lone `.yml` loads fine and then fails at scrape time.

Before committing a change to any scraper:

```bash
./validate.py
```

It checks each `.yml` against what Stash actually accepts — action names must be one of
`script`, `stash`, `scrapeXPath`, `scrapeJson`; `sceneByURL`/`groupByURL` must be lists
while `sceneByName`/`sceneByQueryFragment` must be single objects; a script must exist next
to its `.yml`. Those mistakes otherwise surface only as a line in the Stash server log.

## Publishing it as a scraper source (optional)

You only need this if you want the scrapers installable and updatable from **inside**
Stash's UI, or want to share them with other people. For your own machine the symlink
above is enough, and `build_site.sh` is not involved at all.

```bash
./build_site.sh          # writes _site/index.yml and one zip per scraper
```

Serve `_site` over HTTP — GitHub Pages from the `gh-pages` branch or `/docs` works — then
in Stash: **Settings → Metadata Providers → Available Scrapers → Add Source**, pointing at
`<your-base-url>/index.yml`.

It versions each package by the last commit that touched its directory, so republishing an
unchanged scraper does not look like an update. A directory with uncommitted changes is
marked `dev`.

## Layout

```
scrapers/<Name>/<Name>.yml      the scraper; its own manifest
scrapers/<Name>/overview.md     what it maps and why
tools/                          companion downloaders
build_site.sh                   packages the repo as a Stash scraper source
```

This mirrors stashapp/CommunityScrapers, so a scraper here can be submitted upstream
unchanged if you ever want to.

## Two things to keep out of git

The `.gitignore` covers both, but they are worth knowing about:

* **`shibbydex-cookies.txt`** is a live session credential. It sits next to whichever
  script uses it and must never be committed.
* **`shibbydex-series.json` / `shibbydex-index.json`** are generated caches that rebuild
  themselves. Committing them just creates churn.

A scraper can also exclude files from its published zip with a `# ignore:` line in its
`.yml` — Shibbydex uses one so a package can never carry the cookie file or the caches.
