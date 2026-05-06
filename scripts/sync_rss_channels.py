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
        if not info_file.exists() or added > 0:
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

    print("\n=== RSS sync complete ===")


if __name__ == "__main__":
    main()
