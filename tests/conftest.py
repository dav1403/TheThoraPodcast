"""Shared pytest fixtures for the generator test-suite.

The generator (scripts/generate_channel_pages.py) is stdlib-only, so we can
import it directly. It reads its inputs from the current working directory
(channels.json, speakers.json, feeds/*.entries.json) and writes home.json /
search-index.json / *.html into the CWD as well. To keep the tests fast and
side-effect-free we never touch the real ~13k-page dataset: instead each test
runs against the small fixtures in tests/fixtures/, copied into a temp dir that
becomes the CWD for the duration of the test.
"""
import importlib
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

sys.path.insert(0, str(SCRIPTS))


@pytest.fixture(scope="session")
def gen():
    """The imported generator module (scripts/generate_channel_pages.py)."""
    return importlib.import_module("generate_channel_pages")


@pytest.fixture
def fixture_workdir(tmp_path, monkeypatch):
    """Copy the fixture dataset into a temp dir and chdir into it.

    Yields the temp Path. channels.json / speakers.json / feeds/ are placed so
    the generator's relative-path constants (CHANNELS_FILE, FEEDS_DIR, ...) all
    resolve inside the sandbox.
    """
    shutil.copy(FIXTURES / "channels.json", tmp_path / "channels.json")
    shutil.copy(FIXTURES / "speakers.json", tmp_path / "speakers.json")
    shutil.copytree(FIXTURES / "feeds", tmp_path / "feeds")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


@pytest.fixture
def channels():
    return _load(FIXTURES / "channels.json")


@pytest.fixture
def speakers():
    return _load(FIXTURES / "speakers.json")


@pytest.fixture
def entries_un():
    return _load(FIXTURES / "feeds" / "rav-test-un.entries.json")


@pytest.fixture
def entries_deux():
    return _load(FIXTURES / "feeds" / "rav-test-deux.entries.json")


@pytest.fixture
def all_data(channels, entries_un, entries_deux):
    """The (channel, entries) list the generator passes around internally,
    limited to the two enabled test channels."""
    by_slug = {c["slug"]: c for c in channels}
    return [
        (by_slug["rav-test-un"], entries_un),
        (by_slug["rav-test-deux"], entries_deux),
    ]


@pytest.fixture
def entries_cache(all_data):
    return {ch["slug"]: entries for ch, entries in all_data}
