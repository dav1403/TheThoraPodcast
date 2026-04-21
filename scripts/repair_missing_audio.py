"""
repair_missing_audio.py
-----------------------
Fixes entries whose audio_url points to a file that doesn't exist in R2.
For each broken entry:
  - If the video is still on YouTube: re-download, upload to R2, update entry.
  - If the video is gone from YouTube: remove the entry from the feed.
Runs in GitHub Actions (ffmpeg + env-var credentials available).
"""
import json, os, subprocess, sys, tempfile, shutil
from pathlib import Path

import boto3
import requests
from botocore.config import Config

sys.stdout.reconfigure(encoding="utf-8")

FEEDS_DIR   = Path("feeds")
BUCKET      = os.environ["R2_BUCKET_NAME"]
PUB_BASE    = os.environ["R2_PUBLIC_URL"].rstrip("/")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "dav1403/TheThoraPodcast")

sys.path.insert(0, str(Path(__file__).parent))
from process_podcasts import (
    get_r2_client, upload_audio_to_r2, build_rss_feed,
    load_feed_entries, save_feed_entries, get_channel_info,
    AUDIO_DIR,
)


def object_exists_in_r2(key: str) -> bool:
    client = get_r2_client()
    try:
        client.head_object(Bucket=BUCKET, Key=key)
        return True
    except Exception:
        return False


def key_from_url(url: str) -> str:
    """Extract R2 object key from a public URL."""
    return url.split(PUB_BASE + "/", 1)[-1] if PUB_BASE + "/" in url else ""


def collect_broken(feeds_dir: Path) -> list[dict]:
    """Return list of broken entries: {feed_path, idx, entry, slug}"""
    broken = []
    for feed_path in sorted(feeds_dir.glob("*.entries.json")):
        slug    = feed_path.stem.replace(".entries", "")
        entries = json.loads(feed_path.read_text(encoding="utf-8"))
        for i, e in enumerate(entries):
            url = e.get("audio_url", "")
            key = key_from_url(url)
            if not key:
                broken.append(dict(feed_path=feed_path, idx=i, entry=e, slug=slug, reason="no_url"))
                continue
            if not object_exists_in_r2(key):
                broken.append(dict(feed_path=feed_path, idx=i, entry=e, slug=slug, reason="missing_r2"))
    return broken


def download_audio(video_id: str, work_dir: Path) -> Path | None:
    url     = f"https://www.youtube.com/watch?v={video_id}"
    out_tpl = str(work_dir / f"{video_id}.%(ext)s")
    cmd     = [
        sys.executable, "-m", "yt_dlp",
        "-x", "--audio-format", "mp3", "--audio-quality", "128K",
        "--no-playlist", "--no-warnings",
        "-o", out_tpl, url,
    ]
    cookies = os.environ.get("YOUTUBE_COOKIES_FILE", "")
    if cookies and Path(cookies).exists():
        cmd += ["--cookies", cookies]

    result = subprocess.run(cmd, timeout=300)
    if result.returncode != 0:
        return None
    for p in work_dir.glob(f"{video_id}.*"):
        if p.suffix == ".mp3":
            return p
    return None


def video_available(video_id: str) -> bool:
    """Quick check via YouTube oEmbed API (no quota cost)."""
    r = requests.get(
        f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json",
        timeout=10,
    )
    return r.status_code == 200


def main():
    print("=== Repair Missing Audio ===\n")
    broken = collect_broken(FEEDS_DIR)
    print(f"Found {len(broken)} broken entries.\n")

    if not broken:
        print("Nothing to fix — all entries have audio in R2.")
        return

    # Load channels.json for RSS rebuild
    channels = {ch["slug"]: ch for ch in json.loads(Path("channels.json").read_text(encoding="utf-8"))}

    work_root   = Path(tempfile.mkdtemp(prefix="torah_repair_"))
    fixed       = 0
    removed     = 0
    failed_ids  = []

    # Group by feed_path so we can update entries atomically
    by_feed: dict[Path, list[dict]] = {}
    for item in broken:
        by_feed.setdefault(item["feed_path"], []).append(item)

    for feed_path, items in by_feed.items():
        slug    = items[0]["slug"]
        entries = json.loads(feed_path.read_text(encoding="utf-8"))
        indices_to_remove = []

        for item in items:
            vid   = item["entry"]["video_id"]
            title = item["entry"]["title"]
            print(f"[{vid}] {title[:60]}")

            if not video_available(vid):
                print(f"  Video not on YouTube — removing entry.")
                indices_to_remove.append(item["idx"])
                removed += 1
                continue

            work_dir = work_root / vid
            work_dir.mkdir(exist_ok=True)

            print(f"  Downloading from YouTube...")
            mp3 = download_audio(vid, work_dir)
            if not mp3:
                print(f"  Download failed — skipping.")
                failed_ids.append(vid)
                shutil.rmtree(work_dir, ignore_errors=True)
                continue

            safe  = "".join(c if c.isalnum() or c in "-_" else "_" for c in title)
            key   = f"{vid}_{safe[:80].strip('_')}.mp3"
            final = work_dir / key
            mp3.rename(final)

            print(f"  Uploading {key[:60]}...")
            new_url = upload_audio_to_r2(final, key)
            shutil.rmtree(work_dir, ignore_errors=True)

            # Patch the entry in memory
            entries[item["idx"]]["audio_url"]   = new_url
            entries[item["idx"]]["file_size"]   = final.stat().st_size if final.exists() else 0
            print(f"  Fixed -> {new_url[:80]}")
            fixed += 1

        # Remove unavailable entries (reverse order to keep indices valid)
        for idx in sorted(indices_to_remove, reverse=True):
            del entries[idx]

        save_feed_entries(feed_path.with_suffix(".xml"), entries)

        # Rebuild RSS
        ch_cfg      = channels.get(slug, {"slug": slug, "podcast_author": slug})
        channel_info = {"title": slug, "description": "", "thumbnail": ""}
        try:
            channel_info = get_channel_info(ch_cfg["youtube_channel_id"])
        except Exception:
            pass
        build_rss_feed(ch_cfg, channel_info, entries, feed_path.with_suffix(".xml"))

    shutil.rmtree(work_root, ignore_errors=True)

    print(f"\n=== Done: {fixed} fixed, {removed} removed, {len(failed_ids)} failed ===")
    if failed_ids:
        print("Failed:", failed_ids)
    if fixed + removed == 0 and failed_ids:
        sys.exit(1)


if __name__ == "__main__":
    main()
