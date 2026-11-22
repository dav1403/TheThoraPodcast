"""verify2 (scripts/site_deploy.py): a stale CDN copy of the previous
deployment is waited out, a mismatch on a copy of THIS deployment is red.

Background (measured 22/11/2026): the GitHub Pages CDN ignores the query
string, every file of a deployment carries Last-Modified = unpack time, and
objects are cached max-age=600.
"""
import hashlib
import json
import sys
from email.utils import formatdate
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import site_deploy  # noqa: E402

T = 1_795_320_000.0          # this deployment (marker Last-Modified)
OLD = T - 3 * 3600           # the previous deployment


def hdr(lm: float | None, age: int = 0, ctype: str = "application/xml", **extra) -> dict:
    h = {"Content-Type": ctype, "Cache-Control": "max-age=600", "Age": str(age), "X-Cache": "HIT"}
    if lm is not None:
        h["Last-Modified"] = formatdate(lm, usegmt=True)
    h.update(extra)
    return h


def test_stale_copy_wait_uses_last_modified_and_age():
    w = site_deploy.stale_copy_wait
    assert w(hdr(OLD, age=100), T) == 505          # expires in 600 - 100 s (+5)
    assert w(hdr(OLD, age=0), T) == 605
    assert w(hdr(OLD, age=9999), T) == 5           # already past max-age: short re-probe
    assert w(hdr(T + 4), T) is None                # same deployment (unpack spread)
    assert w(hdr(T - 30), T) is None               # within the margin
    assert w(hdr(None), T) is None                 # no header: cannot tell
    assert w(hdr(OLD), None) is None               # no marker Last-Modified: cannot tell


def test_retry_decision():
    d = site_deploy.retry_decision
    assert d(hdr(OLD, age=100), T, 0, 0, False) == ("stale", 505)
    assert d(hdr(OLD), T, site_deploy.STALE_MAX_WAITS, 0, False) == ("give-up", 0)
    assert d(hdr(T), T, 0, 0, False) == ("fresh", site_deploy.FRESH_MISMATCH_RETRY)
    assert d(hdr(T), T, 0, 0, True) == ("fresh-final", 0)
    assert d(hdr(None), T, 0, 0, False) == ("generic", site_deploy.VERIFY_RETRY_DELAYS[0])
    assert d(hdr(None), T, 0, site_deploy.VERIFY_RETRIES, False) == ("give-up", 0)


GOOD = b"<rss>new</rss>"
OLD_BODY = b"<rss>old</rss>"


class FakeSite:
    """Serves a deployment; `feed_answers` is the sequence of (body, headers)
    returned for feeds/a.xml, the last one repeating."""

    def __init__(self, feed_answers):
        self.feed_answers = list(feed_answers)
        self.marker = {"run_id": "42", "state_generation": 7, "main_sha": "abc",
                       "feeds_md5": {"a.xml": hashlib.md5(GOOD).hexdigest()}}
        self.feed_probes = 0

    def fetch(self, url, timeout=30):
        path = url.split("://", 1)[1].split("/", 1)[1].split("?", 1)[0]
        if path == "_deploy.json":
            return 200, hdr(T, ctype="application/json"), json.dumps(self.marker).encode()
        if path == "feeds/a.xml":
            self.feed_probes += 1
            body, h = self.feed_answers[min(self.feed_probes, len(self.feed_answers)) - 1]
            return 200, h, body
        if path.endswith(".json"):
            body = b'[{"slug": "a"}]' if path == "channels.json" else b"{}"
            return 200, hdr(T, ctype="application/json"), body
        if path.endswith(".webp"):
            return 200, hdr(T, ctype="image/webp"), b"webp"
        if path.endswith(".xml"):
            return 200, hdr(T), b"<urlset/>"
        return 200, hdr(T, ctype="text/html"), b"<html></html>"


@pytest.fixture
def run_verify(tmp_path, monkeypatch):
    slept: list[int] = []
    monkeypatch.setattr(site_deploy.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(site_deploy, "ref_tree_names", lambda *_a: {})
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    legacy = tmp_path / "legacy.txt"
    legacy.write_text("orphan/page.html\n", encoding="utf-8")

    def go(site: FakeSite) -> int:
        monkeypatch.setattr(site_deploy, "fetch", site.fetch)
        args = SimpleNamespace(base="https://example.test", repo=str(tmp_path), run_id="42",
                               timeout=60, legacy_manifest="", legacy_list=str(legacy))
        return site_deploy.cmd_verify2(args)

    go.slept = slept
    return go


def test_stale_cdn_copy_is_waited_out_then_green(run_verify, capsys):
    site = FakeSite([(OLD_BODY, hdr(OLD, age=100)), (GOOD, hdr(T, age=0))])
    assert run_verify(site) == 0
    assert run_verify.slept == [505]               # exactly until the stale copy expires
    out = capsys.readouterr().out
    assert "stale CDN copy of an older deployment" in out
    assert "waited out: 1 (feeds/a.xml)" in out


def test_mismatch_on_a_copy_of_this_deployment_is_red(run_verify, capsys):
    # A real regression: the copy belongs to this deployment and still differs.
    site = FakeSite([(OLD_BODY, hdr(T + 2, age=0))])
    assert run_verify(site) == 1
    assert run_verify.slept == [site_deploy.FRESH_MISMATCH_RETRY]   # one short re-probe only
    out = capsys.readouterr().out
    assert "FAIL: feeds/a.xml: MD5 differs from the build (copy of THIS deployment" in out


def test_stale_copy_that_never_expires_is_red(run_verify):
    site = FakeSite([(OLD_BODY, hdr(OLD, age=0))])
    assert run_verify(site) == 1                   # bounded: STALE_MAX_WAITS / wait budget
    assert 0 < len(run_verify.slept) <= site_deploy.STALE_MAX_WAITS
    assert sum(run_verify.slept) <= site_deploy.VERIFY_WAIT_BUDGET


def test_all_good_no_wait(run_verify):
    assert run_verify(FakeSite([(GOOD, hdr(T))])) == 0
    assert run_verify.slept == []
