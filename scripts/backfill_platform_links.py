#!/usr/bin/env python3
"""
Backfill platform-specific episode links (Spotify, Apple, Deezer)
into feeds/<slug>.entries.json files.

For each active channel with a 'platforms' field, queries the platform API
(or scrapes the page) to find recent episodes, fuzzy-matches their titles
against existing feed entries, and writes the matched episode URLs back to
the entries file under a 'platform_links' key.

Rules:
  - Never overwrite an already-set URL
  - Similarity threshold: 0.70 (difflib.SequenceMatcher on normalized titles)
  - If a platform fetch fails (403, JS-wall, timeout), log and continue
  - By default, fetch episodes from the last --lookback-days days (default 30)
"""

import argparse
import json
import logging
import re
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

import requests

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).parent.parent
CHANNELS_FILE = REPO_ROOT / "channels.json"
FEEDS_DIR = REPO_ROOT / "feeds"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SIMILARITY_THRESHOLD = 0.70
REQUEST_TIMEOUT = 15
INTER_CHANNEL_DELAY = 1.5  # seconds between channels (be polite)

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Title normalization & fuzzy match
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    """Lowercase, strip accents, collapse punctuation/whitespace."""
    text = text.lower().strip()
    # Strip combining characters (accents)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    # Replace any non-word character with a space
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def _best_match(
    platform_title: str, entries: list[dict]
) -> tuple[Optional[dict], float]:
    """Return (best_entry, score). Returns (None, best_score) if below threshold."""
    best_entry, best_score = None, 0.0
    for entry in entries:
        score = _similarity(platform_title, entry.get("title", ""))
        if score > best_score:
            best_score = score
            best_entry = entry
    if best_score >= SIMILARITY_THRESHOLD:
        return best_entry, best_score
    return None, best_score


# ---------------------------------------------------------------------------
# URL / ID helpers
# ---------------------------------------------------------------------------

def _resolve_redirect(url: str) -> str:
    """Follow HTTP redirects to get the final URL (for Deezer short links, etc.)."""
    try:
        r = requests.head(
            url, headers=BROWSER_HEADERS, timeout=REQUEST_TIMEOUT, allow_redirects=True
        )
        return r.url
    except Exception:
        return url


def _extract_show_id(platform_url: str, platform: str) -> Optional[str]:
    if platform == "apple":
        m = re.search(r"/id(\d+)", platform_url)
        return m.group(1) if m else None
    if platform == "deezer":
        m = re.search(r"/show/(\d+)", platform_url)
        return m.group(1) if m else None
    if platform == "spotify":
        m = re.search(r"/show/([A-Za-z0-9]+)", platform_url)
        return m.group(1) if m else None
    return None


def _parse_date(raw: str) -> Optional[datetime]:
    """Parse various date string formats into an aware UTC datetime."""
    if not raw:
        return None
    # ISO format with Z or offset
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(raw.rstrip("Z"), fmt.rstrip("Z"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    # Unix timestamp string
    try:
        return datetime.fromtimestamp(int(raw), tz=timezone.utc)
    except (ValueError, OSError):
        return None


# ---------------------------------------------------------------------------
# Platform fetchers
# ---------------------------------------------------------------------------

def fetch_apple_episodes(show_id: str, cutoff: datetime) -> list[dict]:
    """iTunes Search API — public, no auth required."""
    url = (
        f"https://itunes.apple.com/lookup"
        f"?id={show_id}&entity=podcastEpisode&limit=50&country=us"
    )
    try:
        r = requests.get(url, headers=BROWSER_HEADERS, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except requests.exceptions.Timeout:
        log.warning("[apple] timeout for show %s — skipping", show_id)
        return []
    except Exception as e:
        log.warning("[apple] fetch failed for show %s: %s", show_id, e)
        return []

    episodes = []
    for item in data.get("results", []):
        if item.get("wrapperType") != "podcastEpisode":
            continue
        dt = _parse_date(item.get("releaseDate", ""))
        if dt and dt < cutoff:
            continue
        title = item.get("trackName", "").strip()
        ep_url = item.get("trackViewUrl", "").strip()
        if title and ep_url:
            # Strip affiliate/at params
            ep_url = re.sub(r"\?.*", "", ep_url)
            episodes.append({"title": title, "url": ep_url, "date": dt})

    return episodes


def fetch_deezer_episodes(show_id: str, cutoff: datetime) -> list[dict]:
    """Deezer public REST API — no auth required."""
    url = f"https://api.deezer.com/podcast/{show_id}/episodes?limit=50"
    try:
        r = requests.get(url, headers=BROWSER_HEADERS, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except requests.exceptions.Timeout:
        log.warning("[deezer] timeout for show %s — skipping", show_id)
        return []
    except Exception as e:
        log.warning("[deezer] fetch failed for show %s: %s", show_id, e)
        return []

    if "error" in data:
        log.warning("[deezer] API error for show %s: %s", show_id, data["error"])
        return []

    episodes = []
    for item in data.get("data", []):
        dt = _parse_date(item.get("available_date", ""))
        if dt and dt < cutoff:
            continue
        ep_id = item.get("id")
        title = item.get("title", "").strip()
        # Use 'link' if present, otherwise build from id
        ep_url = item.get("link") or (f"https://www.deezer.com/episode/{ep_id}" if ep_id else "")
        if title and ep_url:
            episodes.append({"title": title, "url": ep_url, "date": dt})

    return episodes


def _get_spotify_client_token() -> Optional[str]:
    """Get an access token via the Client Credentials OAuth flow (no user login needed).
    Requires SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET env vars."""
    import base64
    import os
    client_id = os.environ.get("SPOTIFY_CLIENT_ID", "").strip()
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        log.warning("[spotify] SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET not set — skipping")
        return None
    creds_b64 = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    try:
        r = requests.post(
            "https://accounts.spotify.com/api/token",
            headers={"Authorization": f"Basic {creds_b64}"},
            data={"grant_type": "client_credentials"},
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        return r.json()["access_token"]
    except Exception as e:
        log.warning("[spotify] token fetch failed: %s", e)
        return None


def _spotify_api_episodes(show_id: str, token: str, cutoff: datetime) -> list[dict]:
    """Call the Spotify Web API with a Client Credentials token."""
    url = f"https://api.spotify.com/v1/shows/{show_id}/episodes?limit=50&market=US"
    try:
        r = requests.get(
            url,
            headers={**BROWSER_HEADERS, "Authorization": f"Bearer {token}"},
            timeout=REQUEST_TIMEOUT,
        )
        if r.status_code in (401, 403):
            log.debug("[spotify] API returned %s — token rejected", r.status_code)
            return []
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log.debug("[spotify] API fetch failed: %s", e)
        return []

    episodes = []
    for item in data.get("items", []) or []:
        if not item:
            continue
        dt = _parse_date(item.get("release_date", ""))
        if dt and dt < cutoff:
            continue
        title = item.get("name", "").strip()
        ep_url = item.get("external_urls", {}).get("spotify", "")
        if not ep_url:
            ep_id = item.get("id", "")
            ep_url = f"https://open.spotify.com/episode/{ep_id}" if ep_id else ""
        if title and ep_url:
            episodes.append({"title": title, "url": ep_url, "date": dt})

    return episodes


def _spotify_next_data_episodes(show_url: str, show_id: str, cutoff: datetime) -> list[dict]:
    """
    Fallback: try to extract episode data from __NEXT_DATA__ in the page HTML.
    Spotify has largely moved away from this, so it often yields nothing.
    """
    try:
        r = requests.get(show_url, headers=BROWSER_HEADERS, timeout=REQUEST_TIMEOUT)
        if r.status_code in (401, 403, 429):
            log.warning("[spotify] HTTP %s on show page — JS/auth wall", r.status_code)
            return []
        r.raise_for_status()
    except requests.exceptions.Timeout:
        log.warning("[spotify] timeout fetching show page for %s", show_id)
        return []
    except Exception as e:
        log.warning("[spotify] page fetch failed for show %s: %s", show_id, e)
        return []

    m = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        r.text,
        re.S,
    )
    if not m:
        log.warning(
            "[spotify] no __NEXT_DATA__ in page for show %s — JS-rendered, skipping",
            show_id,
        )
        return []

    try:
        next_data = json.loads(m.group(1))
    except json.JSONDecodeError:
        log.warning("[spotify] could not parse __NEXT_DATA__ for show %s", show_id)
        return []

    episodes = []

    def _walk(obj: object, depth: int = 0) -> None:
        if depth > 25:
            return
        if isinstance(obj, dict):
            uri = obj.get("uri", "")
            name = obj.get("name", "")
            release = obj.get("releaseDate") or obj.get("release_date", "")
            if uri.startswith("spotify:episode:") and name:
                ep_id = uri.split(":")[-1]
                dt = _parse_date(release)
                if dt is None or dt >= cutoff:
                    episodes.append(
                        {
                            "title": name,
                            "url": f"https://open.spotify.com/episode/{ep_id}",
                            "date": dt,
                        }
                    )
            for v in obj.values():
                _walk(v, depth + 1)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item, depth + 1)

    _walk(next_data)
    return episodes


def fetch_spotify_episodes(
    show_id: str, show_url: str, cutoff: datetime
) -> list[dict]:
    """
    Fetch episodes via the Spotify Web API (Client Credentials flow).
    Falls back to __NEXT_DATA__ page scraping if credentials are missing.
    Returns [] gracefully if both strategies fail.
    """
    token = _get_spotify_client_token()
    if token:
        return _spotify_api_episodes(show_id, token, cutoff)

    return _spotify_next_data_episodes(show_url, show_id, cutoff)


# ---------------------------------------------------------------------------
# Per-channel processing
# ---------------------------------------------------------------------------

FETCHERS = {
    "apple": fetch_apple_episodes,
    "deezer": fetch_deezer_episodes,
}


def process_channel(channel: dict, cutoff: datetime, dry_run: bool) -> int:
    slug = channel["slug"]
    platforms: dict = channel.get("platforms", {})

    entries_path = FEEDS_DIR / f"{slug}.entries.json"
    if not entries_path.exists():
        log.warning("[%s] entries file not found — skipping", slug)
        return 0

    with open(entries_path, encoding="utf-8") as f:
        entries: list[dict] = json.load(f)

    total_new = 0

    for platform_name in ("spotify", "apple", "deezer"):
        platform_url: str = platforms.get(platform_name, "")
        if not platform_url:
            continue

        # Resolve short-links (e.g. deezer link.deezer.com/s/...)
        resolved_url = platform_url
        if "link.deezer.com" in platform_url or not re.search(r"/show/\d+|/show/[A-Za-z0-9]+", platform_url):
            resolved_url = _resolve_redirect(platform_url)

        show_id = _extract_show_id(resolved_url, platform_name)
        if not show_id:
            log.warning(
                "[%s][%s] cannot extract show ID from %s — skipping",
                slug, platform_name, resolved_url,
            )
            continue

        log.info("[%s][%s] fetching episodes (show=%s)…", slug, platform_name, show_id)

        if platform_name == "spotify":
            platform_eps = fetch_spotify_episodes(show_id, resolved_url, cutoff)
        else:
            platform_eps = FETCHERS[platform_name](show_id, cutoff)

        if not platform_eps:
            log.info("[%s][%s] no episodes retrieved", slug, platform_name)
            continue

        log.info(
            "[%s][%s] %d episode(s) found on platform",
            slug, platform_name, len(platform_eps),
        )

        matched_count = 0
        skipped_count = 0
        unmatched: list[tuple[str, float]] = []

        for ep in platform_eps:
            ep_title: str = ep["title"]
            ep_url: str = ep["url"]

            entry, score = _best_match(ep_title, entries)

            if entry is None:
                unmatched.append((ep_title, score))
                log.debug(
                    "[%s][%s] NO MATCH (best=%.2f): %r",
                    slug, platform_name, score, ep_title,
                )
                continue

            # Ensure sub-dict exists
            if "platform_links" not in entry:
                entry["platform_links"] = {}

            existing = entry["platform_links"].get(platform_name)
            if existing:
                skipped_count += 1
                log.debug(
                    "[%s][%s] SKIP (already set, score=%.2f): %r → %r",
                    slug, platform_name, score, ep_title, entry["title"],
                )
                continue

            entry["platform_links"][platform_name] = ep_url
            matched_count += 1
            log.info(
                "[%s][%s] MATCH (score=%.2f): %r → %r",
                slug, platform_name, score, ep_title, entry["title"],
            )

        # Summary line for this platform
        unmatched_summary = ", ".join(
            f"{t!r}={s:.2f}" for t, s in unmatched[:5]
        )
        log.info(
            "[%s][%s] done — matched=%d  skipped_existing=%d  unmatched=%d%s",
            slug, platform_name, matched_count, skipped_count, len(unmatched),
            f" (top unmatched: {unmatched_summary})" if unmatched else "",
        )

        total_new += matched_count

    if total_new > 0:
        if not dry_run:
            with open(entries_path, "w", encoding="utf-8") as f:
                json.dump(entries, f, ensure_ascii=False, indent=2)
            log.info("[%s] wrote %d new platform link(s) → %s", slug, total_new, entries_path.name)
        else:
            log.info("[%s] DRY-RUN: would write %d new platform link(s)", slug, total_new)
    else:
        log.info("[%s] nothing to update", slug)

    return total_new


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill Spotify/Apple/Deezer episode links into entries JSON files"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and match but do not write any changes",
    )
    parser.add_argument(
        "--channel",
        metavar="SLUG",
        help="Process only this channel slug",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=30,
        metavar="N",
        help="Only fetch platform episodes published in the last N days (default: 30; use 365 for full backfill)",
    )
    args = parser.parse_args()

    cutoff = datetime.now(timezone.utc) - timedelta(days=args.lookback_days)
    log.info("Lookback window: %d days (cutoff: %s)", args.lookback_days, cutoff.date())

    with open(CHANNELS_FILE, encoding="utf-8") as f:
        channels: list[dict] = json.load(f)

    eligible = [
        c for c in channels
        if c.get("enabled", True)
        and c.get("platforms")
        and any(c["platforms"].get(p) for p in ("spotify", "apple", "deezer"))
    ]

    if args.channel:
        eligible = [c for c in eligible if c["slug"] == args.channel]
        if not eligible:
            log.error("Channel %r not found or not eligible", args.channel)
            return

    log.info("Processing %d channel(s)…", len(eligible))
    if args.dry_run:
        log.info("DRY-RUN mode — no files will be modified")

    grand_total = 0
    for i, channel in enumerate(eligible):
        grand_total += process_channel(channel, cutoff=cutoff, dry_run=args.dry_run)
        if i < len(eligible) - 1:
            time.sleep(INTER_CHANNEL_DELAY)

    log.info("Finished. Total new platform links added: %d", grand_total)


if __name__ == "__main__":
    main()
