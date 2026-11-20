import datetime
import importlib.util
import re
import shutil
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


def test_release_refuses_empty_notes_and_non_increasing_versions(repo):
    shutil.copy(repo / "CHANGELOG.md", repo / "full.md")
    (repo / "CHANGELOG.md").write_text("# Changelog\n\n## [Unreleased]\n\n## [1.3.1] - 2026-11-20\n")
    with pytest.raises(SystemExit, match="empty"):
        release_version.release(repo, "patch", datetime.date.today())

    shutil.copy(repo / "full.md", repo / "CHANGELOG.md")
    with pytest.raises(SystemExit, match="not above"):
        release_version.release(repo, "1.3.1", datetime.date.today())
    with pytest.raises(SystemExit, match="unknown bump"):
        release_version.release(repo, "huge", datetime.date.today())
    assert 'version = "1.3.1"' in (repo / "pyproject.toml").read_text()  # nothing written on refusal
