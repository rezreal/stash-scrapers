#!/bin/bash
# Package this repository as a Stash scraper source.
#
#   ./build_site.sh [outdir]        # default: _site
#
# Produces the layout Stash's scraper manager expects:
#
#   _site/index.yml          one entry per scraper
#   _site/<id>.zip           the scraper directory, minus anything it declares as ignored
#
# Serve _site over HTTP (GitHub Pages works) and add <base-url>/index.yml in Stash under
# Settings > Metadata Providers > Available Scrapers > Add Source.
set -euo pipefail

outdir="${1:-_site}"
rm -rf "$outdir"; mkdir -p "$outdir"
: > "$outdir/index.yml"

for yml in scrapers/*/*.yml; do
    dir=$(dirname "$yml")
    id=$(basename "$yml" .yml)

    name=$(sed -n 's/^name:[[:space:]]*//p' "$yml" | head -1 | sed -e 's/\r//' -e 's/^"\(.*\)"$/\1/')
    # A scraper can exclude files from its package with a "# ignore: <patterns>" line —
    # generated caches, credentials, anything that is not part of the scraper.
    ignore=$(sed -n 's/^# ignore:[[:space:]]*//p' "$yml" | head -1 | sed 's/\r//')
    requires=$(sed -n 's/^# requires:[[:space:]]*//p' "$yml" | head -1 | sed 's/\r//')

    # Version the package by the last commit that touched it, so republishing an
    # unchanged scraper does not look like an update.
    if git rev-parse --git-dir >/dev/null 2>&1 && [ -z "$(git status --porcelain "$dir")" ]; then
        # tformat (not format) so the line ends in a newline — read returns non-zero at
        # EOF without one, and set -e would take that as a failure.
        IFS='|' read -r version updated < <(TZ=UTC0 git log -n1 --date='format-local:%F %T' --pretty=tformat:'%h|%ad' -- "$dir")
    else
        version="dev"; updated=$(TZ=UTC0 date '+%F %T')
        echo "  (uncommitted changes in $dir — marking version 'dev')" >&2
    fi

    # Zipped with python3 rather than the zip(1) binary: python3 is already required by
    # the Shibbydex scraper, zip(1) is not installed everywhere, and fnmatch gives the
    # "# ignore:" patterns the same meaning on every platform.
    zipfile=$(realpath "$outdir/$id.zip")
    ZIP_OUT="$zipfile" ZIP_DIR="$dir" ZIP_IGNORE="$ignore" python3 - <<'PYZIP'
import fnmatch, os, zipfile
out, root = os.environ["ZIP_OUT"], os.environ["ZIP_DIR"]
patterns = os.environ.get("ZIP_IGNORE", "").split()
def skipped(rel):
    return any(fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(os.path.basename(rel), p)
               for p in patterns)
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in sorted(files):
            rel = os.path.relpath(os.path.join(base, f), root)
            if not skipped(rel):
                z.write(os.path.join(base, f), rel)
PYZIP

    printf -- '- id: %s\n  name: %s\n  version: %s\n  date: %s\n  path: %s.zip\n  sha256: %s\n' \
        "$id" "$name" "$version" "$updated" "$id" "$(sha256sum "$zipfile" | cut -d' ' -f1)" >> "$outdir/index.yml"
    if [ -n "$requires" ]; then
        echo "  requires:" >> "$outdir/index.yml"
        for d in ${requires//,/ }; do echo "    - $d" >> "$outdir/index.yml"; done
    fi
    echo >> "$outdir/index.yml"

    echo "packaged $id ($name) $version"
done

echo "wrote $outdir/index.yml"
