"""
sync_rss_channels.py
--------------------
Fetches and syncs episodes from external RSS-hosted channels (Anchor/Spotify).
Audio is linked directly from RSS enclosure URLs — nothing is downloaded to R2.
Saves entries to feeds/<slug>.entries.json and channel metadata to
feeds/<slug>.channel_info.json. Downloads artwork to artwork/<slug>.png.
"""
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
import xml.etree.ElementTree as ET

sys.stdout.reconfigure(encoding="utf-8")

import requests

FEEDS_DIR     = Path("feeds")
ARTWORK_DIR   = Path("artwork")
ITUNES_NS     = "http://www.itunes.com/dtds/podcast-1.0.dtd"
HEALTH_FILE   = FEEDS_DIR / "rss_health.json"

# Number of most-recent episodes to spot-check per channel
HEALTH_SAMPLE = 5


def strip_html(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "").strip()


def parse_duration(s: str) -> int:
    if not s:
        return 0
    parts = s.strip().split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        return int(parts[0])
    except Exception:
        return 0


def fetch_feed(rss_url: str):
    r = requests.get(rss_url, timeout=20, headers={"User-Agent": "TheTorahPodcast/1.0"})
    r.raise_for_status()
    return ET.fromstring(r.content)


def parse_channel(root: ET.Element) -> tuple[dict, list[dict]]:
    channel = root.find("channel")

    # Channel-level artwork
    ch_image = ""
    img_el = channel.find(f"{{{ITUNES_NS}}}image")
    if img_el is not None:
        ch_image = img_el.get("href", "")

    description = strip_html(channel.findtext("description") or "")
    if not description:
        description = strip_html(
            channel.findtext(f"{{{ITUNES_NS}}}summary") or ""
        )

    meta = {"description": description, "artwork_url": ch_image}

    entries = []
    for item in channel.findall("item"):
        title = strip_html(item.findtext("title") or "")
        guid  = (item.findtext("guid") or "").strip()
        pub_raw = (item.findtext("pubDate") or "").strip()
        desc  = strip_html(item.findtext("description") or "")

        enclosure = item.find("enclosure")
        if enclosure is None:
            continue
        audio_url = enclosure.get("url", "")
        file_size = int(enclosure.get("length") or 0)

        duration_secs = parse_duration(
            item.findtext(f"{{{ITUNES_NS}}}duration") or ""
        )

        ep_img_el = item.find(f"{{{ITUNES_NS}}}image")
        thumb = ep_img_el.get("href", ch_image) if ep_img_el is not None else ch_image

        if not title or not audio_url:
            continue

        try:
            pub_dt = parsedate_to_datetime(pub_raw)
            published = pub_dt.isoformat()
        except Exception:
            published = datetime.now(timezone.utc).isoformat()

        entries.append({
            "video_id":     guid,
            "title":        title,
            "description":  desc,
            "published":    published,
            "audio_url":    audio_url,
            "file_size":    file_size,
            "duration_secs": duration_secs,
            "thumbnail":    thumb,
            "external":     True,
        })

    return meta, entries


def download_artwork(url: str, dest: Path) -> bool:
    if not url or dest.exists():
        return False
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            data = r.read()
        dest.write_bytes(data)
        return True
    except Exception as e:
        print(f"    Artwork download failed: {e}")
        return False


def check_audio_urls(slug: str, entries: list[dict]) -> dict:
    """HEAD-check the HEALTH_SAMPLE most recent audio URLs. Returns a health record."""
    sample = [ep for ep in entries if ep.get("audio_url")][:HEALTH_SAMPLE]
    failures = []
    for ep in sample:
        url = ep["audio_url"]
        try:
            r = requests.head(url, timeout=10, allow_redirects=True,
                              headers={"User-Agent": "TheTorahPodcast/1.0"})
            if r.status_code >= 400:
                failures.append({"url": url, "status": r.status_code,
                                  "title": ep.get("title", "")[:60]})
        except Exception as e:
            failures.append({"url": url, "status": "error", "error": str(e),
                              "title": ep.get("title", "")[:60]})

    status = "ok" if not failures else "degraded"
    record = {
        "slug":       slug,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "sample":     len(sample),
        "failures":   failures,
        "status":     status,
    }
    if failures:
        print(f"  [{slug}] HEALTH WARNING — {len(failures)}/{len(sample)} URLs failed:")
        for f in failures:
            print(f"    [{f['status']}] {f['url'][:80]}")
    else:
        print(f"  [{slug}] health OK ({len(sample)} URLs checked)")
    return record


def main():
    channels = json.loads(Path("channels.json").read_text(encoding="utf-8-sig"))
    rss_channels = [
        ch for ch in channels
        if ch.get("source") == "rss" and ch.get("enabled", True)
    ]

    if not rss_channels:
        print("No external RSS channels configured.")
        return

    print(f"=== Sync RSS Channels ({len(rss_channels)} channel(s)) ===")
    FEEDS_DIR.mkdir(exist_ok=True)
    ARTWORK_DIR.mkdir(exist_ok=True)

    # Load existing health records so we can merge and persist
    health_records: dict[str, dict] = {}
    if HEALTH_FILE.exists():
        try:
            health_records = {
                r["slug"]: r
                for r in json.loads(HEALTH_FILE.read_text(encoding="utf-8"))
            }
        except Exception:
            pass

    any_degraded = False

    for ch in rss_channels:
        slug    = ch["slug"]
        rss_url = ch["rss_url"]
        print(f"  [{slug}] Fetching {rss_url}")

        try:
            root = fetch_feed(rss_url)
        except Exception as e:
            print(f"  [{slug}] ERROR fetching feed: {e}")
            continue

        meta, new_entries = parse_channel(root)

        # Load existing entries (keyed by guid)
        entries_file = FEEDS_DIR / f"{slug}.entries.json"
        existing = {}
        if entries_file.exists():
            for ep in json.loads(entries_file.read_text(encoding="utf-8")):
                existing[ep["video_id"]] = ep

        # RSS feed is authoritative; merge preserves any extra local fields
        merged = {ep["video_id"]: ep for ep in new_entries}
        for vid, ep in existing.items():
            if vid not in merged:
                merged[vid] = ep

        result = sorted(merged.values(), key=lambda x: x["published"], reverse=True)
        entries_file.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        added = len(merged) - len(existing)
        print(f"  [{slug}] {len(result)} episodes ({added:+d} new)")

        # Save channel_info for render_page to pick up description + SEO
        info_file = FEEDS_DIR / f"{slug}.channel_info.json"
        if True:  # always refresh so description and SEO stay up to date
            info = {
                "description":    meta["description"],
                "seo_description": meta["description"][:155] if meta["description"] else "",
                "page_description": meta["description"],
            }
            info_file.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")

        # Download artwork once
        art_dest = ARTWORK_DIR / f"{slug}.png"
        if download_artwork(meta["artwork_url"], art_dest):
            print(f"  [{slug}] Artwork saved → {art_dest}")

        # Audio URL health check
        health = check_audio_urls(slug, result)
        health_records[slug] = health
        if health["status"] != "ok":
            any_degraded = True

    # Persist health records
    HEALTH_FILE.write_text(
        json.dumps(list(health_records.values()), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("\n=== RSS sync complete ===")
    if any_degraded:
        print("WARNING: one or more external channels have broken audio URLs — check feeds/rss_health.json")


if __name__ == "__main__":
    main()
