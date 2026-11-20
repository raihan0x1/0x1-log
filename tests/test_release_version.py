import datetime
import importlib.util
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import ox1log

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("release_version", ROOT / "scripts" / "release_version.py")
assert _spec and _spec.loader
release_version = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release_version)


def test_version_is_the_same_everywhere():
    # publish.yml releases whatever pyproject says; a hand edit that skips
    # `make release-version` fails here before anything reaches PyPI.
    version = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(), re.MULTILINE).group(1)
    assert ox1log.__version__ == version
    assert re.search(rf"^## \[{re.escape(version)}\] - ", (ROOT / "CHANGELOG.md").read_text(), re.MULTILINE)


EMPTY_CHANGELOG = "# Changelog\n\n## [Unreleased]\n\n## [1.3.1] - 2026-11-20\n\n- old\n"


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "ox1log").mkdir()
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "0x1-log"\nversion = "1.3.1"\n')
    (tmp_path / "ox1log" / "__init__.py").write_text('"""doc"""\n__version__ = "1.3.1"\n')
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [Unreleased]\n\n### Fixed\n- a fix\n\n## [1.3.1] - 2026-11-20\n\n- old\n"
    )
    return tmp_path


def test_release_moves_unreleased_notes_under_the_new_version(repo):
    assert release_version.release(repo, "minor", datetime.date(2026, 12, 1)) == ("1.3.1", "1.4.0")

    assert 'version = "1.4.0"' in (repo / "pyproject.toml").read_text()
    assert '__version__ = "1.4.0"' in (repo / "ox1log" / "__init__.py").read_text()
    assert (repo / "CHANGELOG.md").read_text() == (
        "# Changelog\n\n## [Unreleased]\n\n## [1.4.0] - 2026-12-01\n\n### Fixed\n- a fix\n\n"
        "## [1.3.1] - 2026-11-20\n\n- old\n"
    )


def test_release_refuses_bad_bumps_and_empty_notes_without_a_tag(repo):
    shutil.copy(repo / "CHANGELOG.md", repo / "full.md")
    (repo / "CHANGELOG.md").write_text(EMPTY_CHANGELOG)
    with pytest.raises(SystemExit, match="no tag v1.3.1"):  # not a git repo: no commits to read
        release_version.release(repo, "patch", datetime.date.today())

    shutil.copy(repo / "full.md", repo / "CHANGELOG.md")
    with pytest.raises(SystemExit, match="not above"):
        release_version.release(repo, "1.3.1", datetime.date.today())
    with pytest.raises(SystemExit, match="unknown bump"):
        release_version.release(repo, "huge", datetime.date.today())
    assert 'version = "1.3.1"' in (repo / "pyproject.toml").read_text()  # nothing written on refusal


def test_notes_from_commits_groups_conventional_subjects():
    notes = release_version.notes_from_commits(
        ["feat(ci): add automated release", "fix: cap ring bytes", "perf(agent): count lists",
         "docs: readme", "chore(release): v1.3.1", "plain subject"]
    )
    assert notes == (
        "### Added\n- Add automated release\n\n"
        "### Fixed\n- Cap ring bytes\n- Count lists\n\n"
        "### Changed\n- Readme\n- Plain subject"
    )


def test_empty_unreleased_takes_notes_from_commits_since_the_tag(repo):
    # `make release-version` right after a release: [Unreleased] is empty (2026-11-20).
    def git(*args):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)

    (repo / "CHANGELOG.md").write_text(EMPTY_CHANGELOG)
    git("init", "-q")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "feat: old")
    git("tag", "v1.3.1")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "feat(ci): add release")

    assert release_version.release(repo, "patch", datetime.date(2026, 11, 20)) == ("1.3.1", "1.3.2")
    assert (repo / "CHANGELOG.md").read_text() == (
        "# Changelog\n\n## [Unreleased]\n\n## [1.3.2] - 2026-11-20\n\n### Added\n- Add release\n\n"
        "## [1.3.1] - 2026-11-20\n\n- old\n"
    )

    git("tag", "v1.3.2")
    (repo / "CHANGELOG.md").write_text(EMPTY_CHANGELOG.replace("1.3.1", "1.3.2"))
    with pytest.raises(SystemExit, match="nothing to release"):
        release_version.release(repo, "patch", datetime.date.today())
