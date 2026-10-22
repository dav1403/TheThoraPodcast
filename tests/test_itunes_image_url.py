"""Tests for the episode <itunes:image> URL guard (scripts/feeds_util.itunes_image_url).

Stdlib-only, like the rest of the CI suite: process_podcasts.py pulls in
feedgen / yt-dlp / boto3 and is not importable in the tests workflow, so the
normalisation lives in feeds_util.py.

Regression covered: on 21/10/2026 YouTube returned
".../maxresdefault.jpg?v=7" as the thumbnail of a rav-meir-david-shmueli
episode. feedgen raised "Image file must be png or jpg", the channel errored,
process_podcasts.py exited 1, and every later step of the "Update Podcast
Feeds" workflow — the R2 state push included — was skipped on every run.
"""
import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

feeds_util = importlib.import_module("feeds_util")


def feedgen_accepts(url: str) -> bool:
    """The exact rule of feedgen 1.0 PodcastEntryExtension.itunes_image()."""
    return url.endswith(".jpg") or url.endswith(".png")


def test_youtube_cache_buster_query_is_stripped():
    """The URL that broke production on 21/10/2026."""
    url = "https://i.ytimg.com/vi/Tl6m8ZYhnYA/maxresdefault.jpg?v=7"
    out = feeds_util.itunes_image_url(url)
    assert out == "https://i.ytimg.com/vi/Tl6m8ZYhnYA/maxresdefault.jpg"
    assert feedgen_accepts(out)


@pytest.mark.parametrize("url", [
    "https://i.ytimg.com/vi/abc/maxresdefault.jpg",
    "https://i.ytimg.com/vi/abc/hqdefault.jpg",
    "https://i.ytimg.com/vi/abc/maxresdefault_live.jpg",
    "https://thetorahpodcast.net/artwork/lev.png",
])
def test_clean_jpg_and_png_urls_are_unchanged(url):
    assert feeds_util.itunes_image_url(url) == url


def test_query_and_fragment_are_both_dropped():
    url = "https://i.ytimg.com/vi/abc/hqdefault.jpg?sqp=xyz&rs=AOn4#frag"
    assert feeds_util.itunes_image_url(url) == "https://i.ytimg.com/vi/abc/hqdefault.jpg"


@pytest.mark.parametrize("url", [
    "https://i.ytimg.com/vi_webp/abc/maxresdefault.webp",
    "https://i.ytimg.com/vi_webp/abc/maxresdefault.webp?v=3",
    "https://yt3.ggpht.com/ytc/AIdro_kX=s800-c-k-c0x00ffffff-no-rj",
    "https://example.com/cover.avif",
    "https://example.com/cover.jpeg",
])
def test_non_jpg_png_images_are_omitted(url):
    """No tag rather than a crash: the episode inherits the channel artwork."""
    assert feeds_util.itunes_image_url(url) is None


@pytest.mark.parametrize("url", [None, "", "   ", "maxresdefault.jpg", "/vi/abc/x.jpg"])
def test_empty_or_relative_values_are_omitted(url):
    assert feeds_util.itunes_image_url(url) is None


@pytest.mark.parametrize("url", [
    "https://i.ytimg.com/vi/abc/maxresdefault.jpg?v=7",
    "https://i.ytimg.com/vi/abc/maxresdefault.jpg",
    "https://i.ytimg.com/vi_webp/abc/maxresdefault.webp",
    "https://example.com/a.png?x=1#y",
    "",
    None,
])
def test_output_never_trips_feedgen(url):
    """Whatever comes in, the value handed to feedgen is accepted or absent."""
    out = feeds_util.itunes_image_url(url)
    assert out is None or feedgen_accepts(out)
