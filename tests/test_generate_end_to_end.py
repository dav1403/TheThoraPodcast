"""End-to-end sanity: run the whole generator over the tiny fixture dataset in a
temp CWD and validate every emitted page. Fast (~2 channels + 2 speakers, a few
dozen pages) — never touches the real ~13k-page catalog."""
import glob
import json
import re

_MUSTACHE_RE = re.compile(r"\{\{|\}\}")


def test_full_generation_produces_clean_pages(gen, fixture_workdir):
    gen.main()

    html_files = glob.glob("**/*.html", recursive=True)
    assert html_files, "generator produced no HTML"

    for path in html_files:
        text = open(path, encoding="utf-8").read()
        assert not _MUSTACHE_RE.search(text), f"residual placeholder in {path}"
        assert text.lstrip().startswith("<!DOCTYPE html>"), f"bad doctype in {path}"
        assert "</html>" in text, f"unterminated html in {path}"

    # Artifacts land next to the pages and are valid JSON.
    home = json.loads((fixture_workdir / "home.json").read_text(encoding="utf-8"))
    index = json.loads((fixture_workdir / "search-index.json").read_text(encoding="utf-8"))
    assert home["recents"]
    assert index

    # Every URL the artifacts advertise resolves to a file that was just written.
    for r in home["recents"]:
        assert (fixture_workdir / r["url"]).exists(), r["url"]
    for e in index:
        assert (fixture_workdir / e["u"]).exists(), e["u"]


def test_generated_channel_page_written_for_each_enabled_channel(gen, fixture_workdir):
    gen.main()
    assert (fixture_workdir / "rav-test-un.html").exists()
    assert (fixture_workdir / "rav-test-deux.html").exists()
    # Disabled channel must NOT be generated.
    assert not (fixture_workdir / "rav-test-disabled.html").exists()
