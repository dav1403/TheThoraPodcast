"""Pure-logic tests for scripts/site_state.py and scripts/site_compare.py
(R2 state mirror + rebuilt-site comparison). No network, no boto3 needed."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import site_compare  # noqa: E402
import site_state  # noqa: E402


def test_state_classification():
    assert site_state.is_state_path("feeds/rav-itshak-cohen.entries.json")
    assert site_state.is_state_path("feeds/transcripts/abc.txt")
    assert site_state.is_state_path("artwork/thumb/.state.json")
    assert site_state.is_state_path("search-fts/.build-state.json")
    assert site_state.is_state_path("processed.json")
    assert site_state.is_state_path("backfill_state.json")
    # social_state.json stays in git; generated outputs are not state
    assert not site_state.is_state_path("social_state.json")
    assert not site_state.is_state_path("home.json")
    assert not site_state.is_state_path("feeds")  # the dir itself
    assert not site_state.is_state_path("feedsx/a.json")


def test_iter_state_files(tmp_path):
    (tmp_path / "feeds" / "transcripts").mkdir(parents=True)
    (tmp_path / "feeds" / "a.entries.json").write_text("[]")
    (tmp_path / "feeds" / "transcripts" / "v1.txt").write_text("")
    (tmp_path / "feeds" / "__pycache__").mkdir()
    (tmp_path / "feeds" / "__pycache__" / "x.pyc").write_text("")
    (tmp_path / "artwork" / "thumb").mkdir(parents=True)
    (tmp_path / "artwork" / "thumb" / ".state.json").write_text("{}")
    (tmp_path / "processed.json").write_text("{}")
    (tmp_path / "home.json").write_text("{}")
    got = site_state.iter_state_files(tmp_path)
    assert got == sorted([
        "artwork/thumb/.state.json",
        "feeds/a.entries.json",
        "feeds/transcripts/v1.txt",
        "processed.json",
    ])


def test_content_types():
    assert site_state.content_type("feeds/x.xml") == "application/xml; charset=utf-8"
    assert site_state.content_type("feeds/x.entries.json").startswith("application/json")
    assert site_state.content_type("feeds/transcripts/x.txt").startswith("text/plain")
    assert site_state.content_type("artwork/thumb/a.webp") == "image/webp"
    assert site_state.content_type("artwork/a.png") == "image/png"


def test_normalize_endpoint():
    n = site_state.normalize_endpoint
    assert n("https://acct.r2.cloudflarestorage.com") == "https://acct.r2.cloudflarestorage.com"
    assert n("https://acct.r2.cloudflarestorage.com/") == "https://acct.r2.cloudflarestorage.com"
    assert n(" https://acct.r2.cloudflarestorage.com/thetorahpodcast\n") == "https://acct.r2.cloudflarestorage.com"


def test_plan_sync_and_guards():
    local = {"a": {"md5": "1", "size": 1}, "b": {"md5": "2", "size": 1}, "c": {"md5": "3", "size": 1}}
    remote = {"a": "1", "b": "x", "d": "4"}
    up, rm = site_state.plan_sync(local, remote)
    assert up == ["b", "c"] and rm == ["d"]
    assert site_state.deletion_allowed(200, 1000)          # floor
    assert not site_state.deletion_allowed(3000, 50000)    # > 5 %
    assert site_state.deletion_allowed(2000, 50000)


def test_is_newer():
    assert not site_state.is_newer(None, 10, "a")
    assert not site_state.is_newer({"commit": "a", "commit_time": 99}, 10, "a")
    assert site_state.is_newer({"commit": "b", "commit_time": 11}, 10, "a")
    assert not site_state.is_newer({"commit": "b", "commit_time": 9}, 10, "a")


def test_sparse_patterns_exclude_state_and_outputs():
    ch = [{"slug": "rav-itshak-cohen"}, {"slug": "Nahal-Haim"}]
    sp = [{"slug": "avi-assouline"}]
    pats = site_state.sparse_patterns(ch, sp)
    assert pats[0] == "/*"
    for p in ("!/feeds/", "!/artwork/", "!/search-fts/", "!/processed.json",
              "!/backfill_state.json", "!/sitemap.xml", "!/home.json", "!/latest.json",
              "!/search-index.json", "!/mobile/*.json", "/mobile/package.json",
              "!/rav-itshak-cohen.html", "!/rav-itshak-cohen/",
              "!/Nahal-Haim.html", "!/nahal-haim.html", "!/Nahal-Haim/", "!/nahal-haim/",
              "!/avi-assouline.html", "!/avi-assouline/"):
        assert p in pats, p
    # re-include must come after the exclusion it overrides
    assert pats.index("/mobile/package.json") > pats.index("!/mobile/*.json")
    assert "!/social_state.json" not in pats


def test_slug_scope():
    s = "rav-itshak-cohen"
    for p in ("rav-itshak-cohen.html", "rav-itshak-cohen/ep-2020-01-01.html",
              "feeds/rav-itshak-cohen.xml", "feeds/rav-itshak-cohen.entries.json",
              "feeds/rav-itshak-cohen.channel_info.json", "artwork/rav-itshak-cohen.png",
              "artwork/thumb/rav-itshak-cohen.webp"):
        assert site_compare.slug_scope(p, s), p
    for p in ("rav-itshak-cohen-x.html", "feeds/transcripts/rav-itshak-cohen.txt",
              "feeds/rav-itshak-cohen2.xml", "index.html", "rav-itshak-cohenx/a.html"):
        assert not site_compare.slug_scope(p, s), p
    # capitalised slug: lowercase page + capitalised dir/feeds
    assert site_compare.slug_scope("nahal-haim.html", "Nahal-Haim")
    assert site_compare.slug_scope("Nahal-Haim.html", "Nahal-Haim")
    assert site_compare.slug_scope("Nahal-Haim/x-2020-01-01.html", "Nahal-Haim")
    assert site_compare.slug_scope("feeds/Nahal-Haim.xml", "Nahal-Haim")


def test_tolerate_generated_at():
    a = json.dumps({"generated_at": "2026-09-28T10:00:00Z", "x": [1]}).encode()
    b = json.dumps({"generated_at": "2026-09-27T09:00:00Z", "x": [1]}).encode()
    c = json.dumps({"generated_at": "2026-09-27T09:00:00Z", "x": [2]}).encode()
    assert site_compare.tolerate("home.json", a, b, set()) == "generated_at"
    assert site_compare.tolerate("home.json", a, c, set()) is None
    # compact separators (mobile/*.json)
    a2 = b'{"generated_at":"2026-09-28T10:00:00+00:00","total":3}\n'
    b2 = b'{"generated_at":"2026-09-01T10:00:00+00:00","total":3}\n'
    assert site_compare.tolerate("mobile/daf.json", a2, b2, set()) == "generated_at"
    # a formatting change is NOT hidden by the mask
    b3 = b'{"generated_at": "2026-09-01T10:00:00+00:00","total":3}\n'
    assert site_compare.tolerate("mobile/daf.json", a2, b3, set()) is None


def test_tolerate_sitemap_lastmod():
    ref = "<url>\n  <loc>a</loc>\n  <lastmod>2026-09-27</lastmod>\n</url>\n<url>\n  <lastmod>2019-01-01</lastmod>\n</url>"
    ok = ref.replace("2026-09-27", "2026-09-28")
    assert site_compare.tolerate("sitemap.xml", ok.encode(), ref.encode(), {"2026-09-28"}) == "lastmod"
    # an episode's real published date changing is NOT tolerated
    bad = ok.replace("2019-01-01", "2019-01-02")
    assert site_compare.tolerate("sitemap.xml", bad.encode(), ref.encode(), {"2026-09-28"}) is None
    # a lastmod not equal to a build date is NOT tolerated
    assert site_compare.tolerate("sitemap.xml", ok.encode(), ref.encode(), {"2026-01-01"}) is None
    # anything else in the sitemap is NOT tolerated
    loc = ok.replace("<loc>a</loc>", "<loc>b</loc>")
    assert site_compare.tolerate("sitemap.xml", loc.encode(), ref.encode(), {"2026-09-28"}) is None
    # other files are never tolerated
    assert site_compare.tolerate("index.html", b"a", b"b", {"2026-09-28"}) is None


def test_tolerate_eol_only():
    assert site_compare.tolerate("a/b.html", b"x\r\ny\r\n", b"x\ny\n", set()) == "eol"
    assert site_compare.tolerate("a/b.html", b"x\ny\n", b"x\r\ny\n", set()) == "eol"
    # a lone CR, or any other change, is NOT an eol difference
    assert site_compare.tolerate("a/b.html", b"x\ry\n", b"x\ny\n", set()) is None
    assert site_compare.tolerate("a/b.html", b"x\r\nz\n", b"x\ny\n", set()) is None


def test_classify():
    site = {"a": "1", "b": "2", "c": "3"}
    ref = {"a": "1", "b": "x", "d": "4"}
    got = site_compare.classify(site, ref)
    assert got == {"identical": ["a"], "different": ["b"], "missing": ["d"], "extra": ["c"]}


def test_legacy_orphans_only_unlisted_html():
    sm = (
        "<url><loc>https://thetorahpodcast.net/rav-itshak-cohen.html</loc></url>\n"
        "<url><loc>https://thetorahpodcast.net/lev/%D7%90-2020-01-01.html</loc></url>\n"
        "<url>\n    <loc>https://thetorahpodcast.net/lev/b-2021-01-01.html</loc>\n</url>"
    )
    listed = site_compare.sitemap_paths(sm)
    assert "lev/א-2020-01-01.html" in listed  # percent-decoded
    assert "lev/b-2021-01-01.html" in listed
    assert site_compare.is_legacy_orphan("rav-itshak-cohen/old-2020-01-01.html", listed)
    assert not site_compare.is_legacy_orphan("lev/b-2021-01-01.html", listed)
    assert not site_compare.is_legacy_orphan("rav-itshak-cohen.html", listed)
    assert not site_compare.is_legacy_orphan("feeds/x.entries.json", listed)
    assert not site_compare.is_legacy_orphan("home.json", listed)
