#!/usr/bin/env python3
"""
fetch_channel_info.py
One-off script: fetches YouTube channel descriptions and saves them to
feeds/<slug>.channel_info.json for use by generate_channel_pages.py.
"""
import json
import os
import sys
import html
import requests
from pathlib import Path

API_KEY = os.environ.get("YOUTUBE_API_KEY")
if not API_KEY:
    print("ERROR: YOUTUBE_API_KEY not set")
    sys.exit(1)

CHANNELS_FILE = Path("channels.json")
FEEDS_DIR     = Path("feeds")

def fetch_channel_info(channel_id: str) -> dict:
    url = (
        f"https://www.googleapis.com/youtube/v3/channels"
        f"?key={API_KEY}&id={channel_id}&part=snippet"
    )
    data = requests.get(url, timeout=15).json()
    if "error" in data:
        raise Exception(data["error"]["message"])
    items = data.get("items", [])
    if not items:
        raise Exception(f"No channel found for {channel_id}")
    snippet = items[0]["snippet"]
    return {
        "title":       html.unescape(snippet["title"]),
        "description": html.unescape(snippet.get("description", "")),
    }

def main():
    channels = json.loads(CHANNELS_FILE.read_text(encoding="utf-8"))
    for ch in channels:
        if not ch.get("enabled"):
            continue
        slug = ch["slug"]
        out  = FEEDS_DIR / f"{slug}.channel_info.json"
        if out.exists():
            print(f"  {slug} — already exists, skipping")
            continue
        print(f"  Fetching {slug}...")
        try:
            info = fetch_channel_info(ch["youtube_channel_id"])
            out.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"    → {info['title']}: {info['description'][:80]}...")
        except Exception as e:
            print(f"    ERROR: {e}")

if __name__ == "__main__":
    main()
