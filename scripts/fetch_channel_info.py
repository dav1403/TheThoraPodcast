#!/usr/bin/env python3
"""
fetch_channel_info.py
One-off script: fetches YouTube channel descriptions, then uses Claude to
generate an SEO-optimised meta description per channel emphasising podcast
advantages (background listening, episode tracking, no ads, no pritsut).
Results saved to feeds/<slug>.channel_info.json.
"""
import json
import os
import sys
import html
import requests
import anthropic
from pathlib import Path

API_KEY       = os.environ.get("YOUTUBE_API_KEY")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY")

if not API_KEY:
    print("ERROR: YOUTUBE_API_KEY not set"); sys.exit(1)
if not ANTHROPIC_KEY:
    print("ERROR: ANTHROPIC_API_KEY not set"); sys.exit(1)

CHANNELS_FILE = Path("channels.json")
FEEDS_DIR     = Path("feeds")

client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)


def fetch_youtube_info(channel_id: str) -> dict:
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


def generate_seo_description(name: str, yt_description: str, lang: str) -> str:
    if lang == "he":
        language_instruction = "Write in Hebrew (עברית)."
        advantages = (
            "האזנה ברקע תוך כדי עשייה, מעקב אחר פרקים שכבר האזנתם, "
            "ללא פרסומות, וללא תוכן פרוץ שעלול להופיע ביוטיוב."
        )
        example = "שיעורי תורה של הרב X — האזינו ברקע, ללא פרסומות וללא תוכן פרוץ. פרקים זמינים בספוטיפיי, Apple Podcasts ודיזר."
    else:
        language_instruction = "Write in French."
        advantages = (
            "écoute en arrière-plan pendant vos activités, suivi des épisodes déjà écoutés, "
            "aucune publicité, et aucun contenu inapproprié (pritsout) qui pourrait apparaître sur YouTube."
        )
        example = "Les cours de Torah du Rav X en podcast — écoutez en arrière-plan, sans pub et sans contenu inapproprié. Disponible sur Spotify, Apple Podcasts et Deezer."

    prompt = f"""You are writing a meta description for a podcast page dedicated to {name}.

YouTube channel description (for inspiration, do not copy):
{yt_description[:600] if yt_description else "(none)"}

Write a compelling meta description (120–155 characters) that:
- Mentions {name} by name
- Highlights these podcast advantages: {advantages}
- {language_instruction}
- Is a single sentence or two short sentences
- Does NOT start with "Découvrez" or "Bienvenue"

Example style (adapt, do not copy): "{example}"

Return ONLY the description text, nothing else."""

    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text.strip().strip('"')


def main():
    channels = json.loads(CHANNELS_FILE.read_text(encoding="utf-8"))
    for ch in channels:
        if not ch.get("enabled"):
            continue
        slug = ch["slug"]
        out  = FEEDS_DIR / f"{slug}.channel_info.json"

        if out.exists():
            existing = json.loads(out.read_text(encoding="utf-8"))
            if existing.get("seo_description"):
                print(f"  {slug} — already has SEO description, skipping")
                continue

        print(f"  {slug}: fetching YouTube info...")
        try:
            info = fetch_youtube_info(ch["youtube_channel_id"])
        except Exception as e:
            print(f"    ERROR fetching YouTube info: {e}")
            continue

        print(f"    Generating SEO description with Claude...")
        try:
            seo_desc = generate_seo_description(
                ch["podcast_author"],
                info["description"],
                ch.get("podcast_language", "fr"),
            )
            info["seo_description"] = seo_desc
            print(f"    → {seo_desc}")
        except Exception as e:
            print(f"    ERROR generating description: {e}")
            info["seo_description"] = ""

        out.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"    Saved to {out}")


if __name__ == "__main__":
    main()
