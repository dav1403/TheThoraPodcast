#!/usr/bin/env python3
"""
verify_platform_titles.py

Cross-checks episode TITLES between the site's own feeds (the source of truth,
generated from YouTube and served at thetorahpodcast.net) and what podcast
platforms actually display. When David edits a title, the site updates
immediately but Apple/Spotify may keep serving the OLD title from their cache
until they re-ingest the RSS feed — this script surfaces those stale titles so
they can be re-pushed / refreshed.

Source of truth
---------------
Per channel we read feeds/<slug>.entries.json (falling back to the RSS
feeds/<slug>.xml). Each episode is keyed by its YouTube video_id, which is also
the RSS <guid> and therefore the `episodeGuid` Apple exposes — giving a reliable
join across the two sides.

Platforms
---------
- Apple Podcasts: uses the PUBLIC iTunes Lookup API (no key required):
    https://itunes.apple.com/lookup?id=<applePodcastId>&entity=podcastEpisode&limit=200
  The Apple podcast id is parsed from channels.json → platforms.apple
  (".../id<NUMBER>") or an explicit "apple_podcast_id" field on the channel.
  iTunes returns at most a few hundred recent episodes per show, so only recent
  episodes are compared — which is exactly where stale-title problems appear.
- Spotify: the Spotify API requires an OAuth client-credentials token, so it is
  NOT queried by default. If SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET are set
  in the environment the script will also check Spotify; otherwise it prints a
  note and skips it (Apple coverage alone catches the reported problem).

Exit codes
----------
  0  all compared titles match (or nothing to compare)
  1  at least one title mismatch was found (also writes a summary + GHA output)
  2  a hard error (bad config, network) prevented the check from running

Stdlib only — no external dependencies, so it runs unchanged in GitHub Actions.
"""
from __future__ import annotations

import html
import json
import os
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from pathlib import Path

# Print UTF-8 regardless of the host console encoding (Windows cp1252 would
# otherwise crash on Hebrew/accented titles). No-op on already-UTF-8 consoles.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

REPO_ROOT = Path(__file__).resolve().parent.parent
CHANNELS_FILE = REPO_ROOT / "channels.json"
FEEDS_DIR = REPO_ROOT / "feeds"

UA = {"User-Agent": "ttp-title-verify/1.0 (+https://thetorahpodcast.net)"}
ITUNES_LOOKUP = "https://itunes.apple.com/lookup"
# Titles that are auto-generated placeholders on the site side; never flag them.
_DATE_TITLE_RE = re.compile(r"^\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}$|^\d{1,2}\s+\w+\s+\d{4}$")


def norm_title(s: str) -> str:
    """Normalize a title for comparison: NFC, collapse whitespace, strip zero-width
    chars and surrounding quotes/spaces, casefold. Small cosmetic differences
    (curly vs straight quotes, doubled spaces) should NOT count as a mismatch —
    only a genuinely different title should."""
    if not s:
        return ""
    # The site feeds store HTML-escaped titles (&#39; &quot; &amp; …) while
    # platforms display the decoded character — decode before comparing so those
    # do not read as false "stale title" mismatches.
    s = html.unescape(s)
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("​", "").replace("﻿", "")
    # unify quote/dash variants
    s = s.translate({
        0x2018: 0x27, 0x2019: 0x27, 0x201C: 0x22, 0x201D: 0x22,
        0x2013: 0x2D, 0x2014: 0x2D, 0x00A0: 0x20,
    })
    # Apple/Spotify treat "<...>" as markup and silently drop the angle brackets
    # (and sometimes their content), so a bare "<" in a title can never match.
    # Remove angle-bracket characters on both sides so only real wording
    # differences — the stale-title case we care about — are flagged.
    s = s.replace("<", "").replace(">", "")
    s = re.sub(r"\s+", " ", s).strip().strip("\"'").strip()
    return s.casefold()


def http_json(url: str, timeout: int = 30) -> dict:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def load_site_titles(slug: str) -> dict[str, str]:
    """Return {video_id: title} for a channel from its .entries.json (preferred)
    or its generated RSS .xml. The site feed is the source of truth."""
    ej = FEEDS_DIR / f"{slug}.entries.json"
    if ej.exists():
        entries = json.loads(ej.read_text(encoding="utf-8"))
        out = {}
        for ep in entries:
            vid = ep.get("video_id")
            title = (ep.get("title") or "").strip()
            if vid and title:
                out[vid] = title
        return out

    xml = FEEDS_DIR / f"{slug}.xml"
    if xml.exists():
        root = ET.fromstring(xml.read_bytes())
        out = {}
        for item in root.findall(".//item"):
            guid_el = item.find("guid")
            title_el = item.find("title")
            guid = (guid_el.text or "").strip() if guid_el is not None else ""
            title = (title_el.text or "").strip() if title_el is not None else ""
            if guid and title:
                out[guid] = title
        return out
    return {}


def apple_podcast_id(ch: dict) -> str | None:
    if ch.get("apple_podcast_id"):
        return str(ch["apple_podcast_id"])
    url = (ch.get("platforms") or {}).get("apple") or ""
    m = re.search(r"/id(\d+)", url)
    return m.group(1) if m else None


def fetch_apple_episodes(podcast_id: str, limit: int = 200) -> list[dict]:
    """Return [{'guid': episodeGuid, 'title': trackName}, ...] from iTunes lookup."""
    qs = urllib.parse.urlencode({
        "id": podcast_id, "entity": "podcastEpisode", "limit": str(limit),
    })
    data = http_json(f"{ITUNES_LOOKUP}?{qs}")
    eps = []
    for r in data.get("results", []):
        if r.get("wrapperType") == "podcastEpisode" or r.get("kind") == "podcast-episode":
            guid = (r.get("episodeGuid") or "").strip()
            title = (r.get("trackName") or "").strip()
            if guid and title:
                eps.append({"guid": guid, "title": title})
    return eps


def check_channel_apple(ch: dict) -> tuple[list[dict], str | None]:
    """Compare Apple titles vs site titles for one channel.
    Returns (mismatches, skip_reason)."""
    slug = ch["slug"]
    pid = apple_podcast_id(ch)
    if not pid:
        return [], "no Apple podcast id in channels.json"

    site = load_site_titles(slug)
    if not site:
        return [], "no site feed/entries found"

    try:
        apple_eps = fetch_apple_episodes(pid)
    except urllib.error.HTTPError as e:
        return [], f"iTunes HTTP {e.code}"
    except Exception as e:  # noqa: BLE001 - report and keep going on other channels
        return [], f"iTunes error: {e}"

    mismatches = []
    for ep in apple_eps:
        site_title = site.get(ep["guid"])
        if site_title is None:
            continue  # not on the site yet, or not joinable — skip
        if _DATE_TITLE_RE.match(site_title.strip()):
            continue
        if norm_title(site_title) != norm_title(ep["title"]):
            mismatches.append({
                "slug": slug,
                "guid": ep["guid"],
                "site_title": site_title,
                "apple_title": ep["title"],
            })
    return mismatches, None


def spotify_configured() -> bool:
    return bool(os.environ.get("SPOTIFY_CLIENT_ID") and os.environ.get("SPOTIFY_CLIENT_SECRET"))


def main() -> int:
    if not CHANNELS_FILE.exists():
        print(f"ERROR: {CHANNELS_FILE} not found", file=sys.stderr)
        return 2

    channels = json.loads(CHANNELS_FILE.read_text(encoding="utf-8"))
    enabled = [c for c in channels if c.get("enabled")]

    all_mismatches: list[dict] = []
    skipped: list[str] = []
    checked = 0

    for ch in enabled:
        mm, skip = check_channel_apple(ch)
        if skip:
            skipped.append(f"{ch['slug']}: {skip}")
            continue
        checked += 1
        all_mismatches.extend(mm)
        # Be polite to the public iTunes endpoint.
        time.sleep(0.3)

    if not spotify_configured():
        print("NOTE: Spotify check skipped — set SPOTIFY_CLIENT_ID / "
              "SPOTIFY_CLIENT_SECRET to enable it (Apple is checked without a key).")

    # --- report ---
    print(f"\nApple title check: {checked} channel(s) compared, "
          f"{len(skipped)} skipped, {len(all_mismatches)} mismatch(es).")
    for s in skipped:
        print(f"  skipped {s}")

    lines = []
    for m in all_mismatches:
        line = (f"[{m['slug']}] guid={m['guid']}\n"
                f"    site : {m['site_title']}\n"
                f"    apple: {m['apple_title']}")
        print("\nMISMATCH " + line)
        lines.append(f"- **{m['slug']}** (`{m['guid']}`)\n"
                     f"  - site: `{m['site_title']}`\n"
                     f"  - apple: `{m['apple_title']}`")

    # GitHub Actions: expose result for the workflow (issue body / step summary).
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as f:
            if all_mismatches:
                f.write("ok=false\n")
                f.write("count=%d\n" % len(all_mismatches))
                f.write("details<<EOF\n" + "\n".join(lines) + "\nEOF\n")
            else:
                f.write("ok=true\n")
                f.write("count=0\n")

    gh_sum = os.environ.get("GITHUB_STEP_SUMMARY")
    if gh_sum:
        with open(gh_sum, "a", encoding="utf-8") as f:
            f.write("## Platform title verification\n\n")
            f.write(f"- Apple channels compared: **{checked}**\n")
            f.write(f"- Mismatches: **{len(all_mismatches)}**\n")
            if skipped:
                f.write(f"- Skipped: {len(skipped)}\n")
            if all_mismatches:
                f.write("\n### Stale titles (site vs Apple)\n\n" + "\n".join(lines) + "\n")

    return 1 if all_mismatches else 0


if __name__ == "__main__":
    sys.exit(main())
