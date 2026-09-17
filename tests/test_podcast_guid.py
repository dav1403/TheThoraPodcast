"""Tests for the <podcast:guid> identifier (scripts/feeds_util.podcast_guid).

Stdlib-only on purpose, like the rest of the CI suite: process_podcasts.py
pulls in feedgen / yt-dlp / boto3 and is therefore not importable in the tests
workflow, so the GUID computation lives in feeds_util.py where it can be tested.

What matters here is that the value never moves. Directories key their listing
on this GUID: if it changed, a feed would be ingested as a second podcast
instead of an update — the exact duplicate this tag exists to prevent.
"""
import importlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

feeds_util = importlib.import_module("feeds_util")


def test_spec_reference_vector():
    """The example published with the podcast:guid spec.

    podnews.net/rss is the canonical test vector; matching it proves we use the
    right namespace UUID and the right name (protocol stripped), rather than
    merely being self-consistent.
    """
    assert feeds_util.podcast_guid("https://podnews.net/rss") == \
        "9b024349-ccf0-5f69-a609-6b82873eab3c"


def test_known_channel_guid():
    """A frozen value for a real feed of this site."""
    assert feeds_util.podcast_guid("https://thetorahpodcast.net/feeds/rav-itshak-cohen.xml") == \
        "59110f4e-cc9d-5169-97bd-fac66090fb5f"


def test_guid_is_stable_across_calls():
    url = "https://thetorahpodcast.net/feeds/lev.xml"
    first = feeds_util.podcast_guid(url)
    second = feeds_util.podcast_guid(url)
    assert first == second
    assert first == "6d2fc4ad-730e-58e5-811d-db6690107d62"


def test_protocol_and_trailing_slash_are_ignored():
    """http, https, no scheme and a trailing slash must all hash the same."""
    expected = feeds_util.podcast_guid("https://podnews.net/rss")
    for variant in ("http://podnews.net/rss", "podnews.net/rss",
                    "https://podnews.net/rss/", "  https://podnews.net/rss  "):
        assert feeds_util.podcast_guid(variant) == expected


def test_distinct_feeds_get_distinct_guids():
    slugs = ["rav-itshak-cohen", "lev", "rav-mrejen", "torah_fr"]
    guids = {feeds_util.podcast_guid(f"https://thetorahpodcast.net/feeds/{s}.xml")
             for s in slugs}
    assert len(guids) == len(slugs)


def test_every_self_hosted_channel_gets_a_guid():
    """Channels whose feed we generate must all yield a well-formed GUID.

    Externally sourced channels ("source": "rss", e.g. an anchor.fm feed) are
    skipped: we do not publish their XML, so we do not mint their identifier.
    """
    channels = json.loads((ROOT / "channels.json").read_text(encoding="utf-8-sig"))
    self_hosted = [c for c in channels if c.get("source") != "rss"]
    assert self_hosted, "expected at least one self-hosted channel"
    for ch in self_hosted:
        guid = feeds_util.podcast_guid(
            f"https://thetorahpodcast.net/feeds/{ch['slug']}.xml")
        parts = guid.split("-")
        assert [len(p) for p in parts] == [8, 4, 4, 4, 12], guid
        assert parts[2][0] == "5", f"{ch['slug']}: not a UUID v5 -> {guid}"


def test_self_hosted_channels_share_the_project_owner_email():
    """<itunes:email> is where directories send ownership validation codes.

    It must be an address the project actually reads, on every channel we
    publish — a per-rabbi address means the code lands in someone else's inbox.
    """
    channels = json.loads((ROOT / "channels.json").read_text(encoding="utf-8-sig"))
    offenders = [c["slug"] for c in channels
                 if c.get("source") != "rss"
                 and c.get("podcast_email") != "thetorahpodcast@gmail.com"]
    assert offenders == [], f"channels off the owner-email convention: {offenders}"
