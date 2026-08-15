"""
fetch_transcripts.py
--------------------
Downloads YouTube auto-captions (VTT) for all episodes, strips timestamps,
and saves clean plain text to feeds/transcripts/<video_id>.txt.

French channels use 'fr' captions, Hebrew channels use 'iw' (YouTube's
legacy ISO code for Hebrew). Falls back to 'en' if the preferred lang
is unavailable.

Budget: number of new transcripts to fetch per run. Episodes that already
have a transcript file (even empty, meaning no captions available) are
skipped immediately with no network call.
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from yt_dlp import YoutubeDL

FEEDS_DIR = Path("feeds")
TRANSCRIPTS_DIR = FEEDS_DIR / "transcripts"

# YouTube uses 'iw' as its internal code for Hebrew (legacy ISO 639-1)
LANG_TO_YT = {"he": "iw", "fr": "fr"}


def vtt_to_text(vtt: str) -> str:
    lines = vtt.splitlines()
    seen, chunks = set(), []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("WEBVTT") or line.startswith("NOTE") or "-->" in line:
            continue
        # Caption metadata header ("Kind: captions", "Language: fr") is not speech.
        if re.match(r"^(Kind|Language):\s", line, re.IGNORECASE):
            continue
        line = re.sub(r"<[^>]+>", "", line).strip()
        if line and line not in seen:
            seen.add(line)
            chunks.append(line)
    return " ".join(chunks)


def fetch_transcript(video_id: str, lang_pref: str) -> str | None:
    ydl_opts = {"quiet": True, "no_warnings": True}
    cookies_file = os.environ.get("YOUTUBE_COOKIES_FILE")
    if cookies_file and Path(cookies_file).exists():
        ydl_opts["cookiefile"] = cookies_file

    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(
                f"https://www.youtube.com/watch?v={video_id}", download=False
            )
            auto = info.get("automatic_captions", {})

        for code in [lang_pref, "fr", "en"]:
            if code not in auto:
                continue
            vtt_entries = [f for f in auto[code] if f["ext"] == "vtt"]
            if not vtt_entries:
                continue
            url = vtt_entries[0]["url"]
            try:
                with urllib.request.urlopen(url, timeout=15) as r:
                    vtt_raw = r.read().decode("utf-8")
                text = vtt_to_text(vtt_raw)
                if text:
                    return text
            except Exception as e:
                print(f"      VTT download error ({code}): {e}")
    except Exception as e:
        print(f"      yt-dlp error: {e}")
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--budget", type=int, default=50)
    args = parser.parse_args()

    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)

    channels = json.loads(Path("channels.json").read_text(encoding="utf-8-sig"))
    # RSS channels have GUIDs, not YouTube video IDs — skip them entirely
    enabled = [ch for ch in channels if ch.get("enabled", True) and ch.get("source") != "rss"]

    print(f"=== Fetch Transcripts — budget: {args.budget} ===")
    fetched = 0

    for ch in enabled:
        if fetched >= args.budget:
            break
        slug = ch["slug"]
        ch_lang = ch.get("podcast_language", "fr")
        yt_lang = LANG_TO_YT.get(ch_lang, "fr")

        entries_file = FEEDS_DIR / f"{slug}.entries.json"
        if not entries_file.exists():
            continue
        entries = json.loads(entries_file.read_text(encoding="utf-8"))
        missing = [
            ep for ep in entries
            if ep.get("video_id")
            and not (TRANSCRIPTS_DIR / f"{ep['video_id']}.txt").exists()
        ]
        if not missing:
            print(f"  [{slug}] all transcripts present, skipping")
            continue
        print(f"  [{slug}] {len(missing)} episodes without transcript (lang={yt_lang})")

        for ep in missing:
            if fetched >= args.budget:
                break
            vid = ep["video_id"]
            out_path = TRANSCRIPTS_DIR / f"{vid}.txt"
            print(f"    {vid}  {ep.get('title', '')[:55]}")
            try:
                text = fetch_transcript(vid, yt_lang)
            except Exception as e:
                print(f"      → ERROR: {e}")
                text = None
            if text:
                out_path.write_text(text, encoding="utf-8")
                print(f"      → {len(text):,} chars saved")
                fetched += 1
            else:
                # Empty file marks episode as checked — skipped on next run
                out_path.write_text("", encoding="utf-8")
                print(f"      → no captions available")
            time.sleep(1.5)

    print(f"\n=== Done: {fetched} new transcript(s) fetched ===")


if __name__ == "__main__":
    main()
