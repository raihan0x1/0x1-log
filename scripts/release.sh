#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

DRY_RUN=0
SKIP_CHECKS=0
AUTO_YES=0
ALLOW_NON_MAIN=0
TARGET=""

usage() {
  cat <<'EOF'
Usage: ./scripts/release.sh [patch|minor|major|X.Y.Z] [options]

Interactive if no version argument is passed. Runs scripts/release_version.py
(bumps pyproject.toml, ox1log/__init__.py, and CHANGELOG.md), runs
./scripts/check.sh, commits the release bump, and pushes to origin so
.github/workflows/publish.yml builds, publishes to PyPI, tags vX.Y.Z, and
creates the GitHub Release.

Options:
  --dry-run         Preview actions without modifying files or remote
  --skip-checks     Skip ./scripts/check.sh before committing and pushing
  -y, --yes         Skip confirmation prompt
  --allow-non-main  Allow releasing from a branch other than main
  -h, --help        Show this help
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --skip-checks) SKIP_CHECKS=1 ;;
    -y|--yes) AUTO_YES=1 ;;
    --allow-non-main) ALLOW_NON_MAIN=1 ;;
    -h|--help)
      usage
      exit 0
      ;;
    -*)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
    *)
      if [ -n "$TARGET" ]; then
        echo "Unexpected extra argument: $1" >&2
        usage >&2
        exit 1
      fi
      TARGET="$1"
      ;;
  esac
  shift
done

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [ "$BRANCH" != "main" ] && [ "$ALLOW_NON_MAIN" -eq 0 ]; then
  echo "ERROR: Current branch is '${BRANCH}'. Switch to 'main' or pass --allow-non-main." >&2
  exit 1
fi

if [ -n "$(git status --porcelain)" ]; then
  echo "ERROR: Working tree has uncommitted changes. Commit or stash them first." >&2
  git status -s >&2
  exit 1
fi

echo "==> Syncing tags from origin"
git fetch --tags origin --quiet

if git rev-parse --verify "origin/${BRANCH}" >/dev/null 2>&1; then
  BEHIND="$(git rev-list --count "HEAD..origin/${BRANCH}")"
  if [ "$BEHIND" -gt 0 ]; then
    echo "ERROR: Local '${BRANCH}' is ${BEHIND} commit(s) behind origin/${BRANCH}. Pull/rebase first." >&2
    exit 1
  fi
fi

CURRENT_VER="$(python3 -c '
import re, pathlib
text = pathlib.Path("pyproject.toml").read_text()
m = re.search(r"^version\s*=\s*\"([^\"]+)\"", text, re.MULTILINE)
if not m:
    raise SystemExit("Could not find version in pyproject.toml")
print(m.group(1))
')"

LATEST_TAG="$(git tag --list 'v*' --sort=-v:refname | head -n 1 || true)"

IFS='.' read -r CUR_MAJOR CUR_MINOR CUR_PATCH <<< "$CURRENT_VER"
NEXT_PATCH="${CUR_MAJOR}.${CUR_MINOR}.$((CUR_PATCH + 1))"
NEXT_MINOR="${CUR_MAJOR}.$((CUR_MINOR + 1)).0"
NEXT_MAJOR="$((CUR_MAJOR + 1)).0.0"

if [ -z "$TARGET" ]; then
  echo ""
  echo "Current version : ${CURRENT_VER}"
  echo "Latest git tag  : ${LATEST_TAG:-none}"
  echo ""
  echo "Choose release version:"
  echo "  1) patch    -> ${NEXT_PATCH}"
  echo "  2) minor    -> ${NEXT_MINOR}"
  echo "  3) major    -> ${NEXT_MAJOR}"
  echo "  4) custom   -> enter X.Y.Z"
  printf "Select [1-4]: "
  read -r CHOICE
  case "$CHOICE" in
    1|patch)   TARGET="patch" ;;
    2|minor)   TARGET="minor" ;;
    3|major)   TARGET="major" ;;
    4|custom)
      printf "Enter version (e.g. 1.5.0): "
      read -r TARGET
      ;;
    *)
      echo "Aborted." >&2
      exit 1
      ;;
  esac
fi

RAW_TARGET="${TARGET#v}"
NEXT_VER="$(python3 - "$CURRENT_VER" "$RAW_TARGET" <<'PY'
import importlib.util, pathlib, sys

spec = importlib.util.spec_from_file_location("release_version", pathlib.Path("scripts/release_version.py"))
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
print(mod.next_version(sys.argv[1], sys.argv[2]))
PY
)"

TAG="v${NEXT_VER}"
if git rev-parse -q --verify "refs/tags/${TAG}" >/dev/null 2>&1; then
  echo "ERROR: Tag '${TAG}' already exists." >&2
  exit 1
fi

if [ "$SKIP_CHECKS" -eq 1 ]; then
  CHECKS_LABEL="skipped (--skip-checks)"
else
  CHECKS_LABEL="./scripts/check.sh"
fi

echo ""
echo "Release plan:"
echo "  Branch  : ${BRANCH}"
echo "  Version : ${CURRENT_VER} -> ${NEXT_VER}"
echo "  Files   : pyproject.toml, ox1log/__init__.py, CHANGELOG.md"
echo "  Commit  : chore(release): bump version to ${NEXT_VER}"
echo "  Checks  : ${CHECKS_LABEL}"
echo "  Push    : git push origin ${BRANCH}"
echo "  Publish : .github/workflows/publish.yml -> PyPI + tag ${TAG} + GitHub Release"
echo ""

if [ "$DRY_RUN" -eq 1 ]; then
  echo "[dry-run] No changes made."
  exit 0
fi

if [ "$AUTO_YES" -eq 0 ]; then
  printf "Proceed to release %s and trigger PyPI publish? [y/N]: " "$NEXT_VER"
  read -r CONFIRM
  case "$CONFIRM" in
    y|Y|yes|YES) ;;
    *)
      echo "Aborted."
      exit 1
      ;;
  esac
fi

BUMPED=0
cleanup_on_err() {
  if [ "$BUMPED" -eq 1 ]; then
    echo "Release failed; reverting version bump files..." >&2
    git checkout -- pyproject.toml ox1log/__init__.py CHANGELOG.md
  fi
}
trap cleanup_on_err ERR

echo "==> Updating version to ${NEXT_VER}"
python3 scripts/release_version.py "$NEXT_VER"
BUMPED=1

if [ "$SKIP_CHECKS" -eq 0 ]; then
  ./scripts/check.sh
fi

git add pyproject.toml ox1log/__init__.py CHANGELOG.md
git commit -m "chore(release): bump version to ${NEXT_VER}"
BUMPED=0

git push origin "$BRANCH"

REPO_URL="$(git remote get-url origin | sed -E 's#\.git$##; s#^git@github\.com:#https://github.com/#')"
echo ""
echo "Pushed ${NEXT_VER} to ${BRANCH}; CI will publish to PyPI and create ${TAG}."
echo "  Actions : ${REPO_URL}/actions"
echo "  PyPI    : https://pypi.org/project/0x1-log/${NEXT_VER}/"
echo "  Release : ${REPO_URL}/releases/tag/${TAG}"
if command -v gh >/dev/null 2>&1; then
  echo "  Watch   : gh run watch"
fi
