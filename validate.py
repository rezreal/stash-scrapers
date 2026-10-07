#!/usr/bin/env python3
"""Check every scraper in this repo against what Stash will actually accept.

Catches the mistakes that only show up as a line in the server log — an invalid
action name, or a single-object config written as a list. Run it before committing:

    ./validate.py
"""

import glob
import os
import sys

import yaml

# pkg/scraper/action.go — scraperAction.IsValid()
VALID_ACTIONS = {"script", "stash", "scrapeXPath", "scrapeJson"}

# pkg/scraper/definition.go — []*ByURLDefinition vs *ByNameDefinition / *ByFragmentDefinition
LIST_KEYS = {"sceneByURL", "galleryByURL", "imageByURL", "movieByURL", "groupByURL",
             "performerByURL"}
SINGLE_KEYS = {"sceneByName", "sceneByQueryFragment", "sceneByFragment", "performerByName",
               "performerByFragment", "galleryByFragment", "imageByFragment"}


def check(path: str) -> list[str]:
    errors = []
    try:
        doc = yaml.safe_load(open(path, encoding="utf-8"))
    except yaml.YAMLError as exc:
        return [f"unparseable: {exc}"]
    if not isinstance(doc, dict):
        return ["not a mapping"]
    if not doc.get("name"):
        errors.append("missing 'name:'")

    where = os.path.dirname(path)
    for key, value in doc.items():
        if key not in LIST_KEYS | SINGLE_KEYS:
            continue
        want_list = key in LIST_KEYS
        if isinstance(value, list) != want_list:
            errors.append(f"{key}: must be a {'list' if want_list else 'single object'}")
        for entry in (value if isinstance(value, list) else [value]):
            if not isinstance(entry, dict):
                errors.append(f"{key}: entry is not a mapping")
                continue
            action = entry.get("action")
            if action not in VALID_ACTIONS:
                errors.append(f"{key}: action {action!r} is not one of "
                              f"{sorted(VALID_ACTIONS)}")
            # A script scraper's command must exist next to the yml, since Stash runs it
            # with the working directory set to the yml's own directory.
            if action == "script":
                for arg in entry.get("script") or []:
                    if str(arg).endswith(".py") and not os.path.exists(os.path.join(where, arg)):
                        errors.append(f"{key}: script {arg!r} is not next to the yml")
    # Stash needs both halves before it will offer search at all.
    if ("sceneByName" in doc) != ("sceneByQueryFragment" in doc):
        errors.append("sceneByName and sceneByQueryFragment must both be present, or neither")
    return errors


def main() -> int:
    root = os.path.dirname(os.path.abspath(__file__))
    failed = 0
    for path in sorted(glob.glob(os.path.join(root, "scrapers", "*", "*.yml"))):
        rel = os.path.relpath(path, root)
        problems = check(path)
        if problems:
            failed += 1
            print(f"FAIL {rel}")
            for p in problems:
                print(f"       {p}")
        else:
            print(f"ok   {rel}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
