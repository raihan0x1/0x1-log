"""Bump the release version in pyproject.toml, ox1log/__init__.py and CHANGELOG.md.

Usage: python3 scripts/release_version.py [patch|minor|major|X.Y.Z]   (or `make release-version`)
The CHANGELOG [Unreleased] notes become the new version's section, so write them first.
Pushing the bumped version to main publishes it to PyPI (.github/workflows/publish.yml).
"""

from __future__ import annotations

import datetime
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_VERSION = re.compile(r"\d+\.\d+\.\d+")


def next_version(current: str, bump: str) -> str:
    if _VERSION.fullmatch(bump):
        new = bump
    else:
        major, minor, patch = map(int, current.split("."))
        steps = {"major": f"{major + 1}.0.0", "minor": f"{major}.{minor + 1}.0", "patch": f"{major}.{minor}.{patch + 1}"}
        if bump not in steps:
            raise SystemExit(f"unknown bump {bump!r}: use patch, minor, major or X.Y.Z")
        new = steps[bump]
    if tuple(map(int, new.split("."))) <= tuple(map(int, current.split("."))):
        raise SystemExit(f"{new} is not above the current {current}")
    return new


def release(root: Path, bump: str, today: datetime.date) -> tuple[str, str]:
    pyproject = root / "pyproject.toml"
    init = root / "ox1log" / "__init__.py"
    changelog = root / "CHANGELOG.md"

    text = pyproject.read_text()
    match = re.search(r'^version = "([^"]+)"', text, re.MULTILINE)
    if match is None:
        raise SystemExit("no version in pyproject.toml")
    current = match.group(1)
    new = next_version(current, bump)

    head, marker, rest = changelog.read_text().partition("## [Unreleased]\n")
    if not marker:
        raise SystemExit("CHANGELOG.md has no '## [Unreleased]' heading")
    notes, older_marker, older = rest.partition("\n## [")
    if not notes.strip():
        raise SystemExit("CHANGELOG [Unreleased] is empty: write this release's notes first")

    pyproject.write_text(text.replace(match.group(0), f'version = "{new}"', 1))
    init.write_text(re.sub(r'^__version__ = "[^"]+"', f'__version__ = "{new}"', init.read_text(), count=1, flags=re.MULTILINE))
    changelog.write_text(
        f"{head}{marker}\n## [{new}] - {today.isoformat()}\n\n{notes.strip()}\n{older_marker}{older}"
    )
    return current, new


if __name__ == "__main__":
    old, new = release(ROOT, sys.argv[1] if len(sys.argv) > 1 else "patch", datetime.date.today())
    print(f"0x1-log {old} -> {new}. Review the diff, commit, push to main: CI publishes {new} to PyPI.")
