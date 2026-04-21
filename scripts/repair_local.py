"""
repair_local.py
---------------
Re-downloads the 22 missing videos (entries with spaces in R2 URLs)
and uploads them to R2 with clean underscored filenames.
Runs locally with hardcoded credentials from check_r2.py.
"""
import json, os, sys, tempfile, subprocess, shutil
from pathlib import Path
import boto3
from botocore.config import Config

sys.stdout.reconfigure(encoding='utf-8')

FEEDS_DIR   = Path(__file__).parent.parent / "feeds"
AUDIO_DIR   = Path(tempfile.mkdtemp(prefix="torah_repair_"))
BUCKET      = "thetorahpodcast"
PUB_BASE    = "https://pub-a5fae25ce5124edebe0bf7393f72823c.r2.dev"
ENDPOINT    = "https://ee46001c5f4e059c54c29105eab205de.r2.cloudflarestorage.com"
ACCESS_KEY  = "1b48c103d43102b53ce39a2d7d68b8a7"
SECRET_KEY  = "584756700a204685f3a8a938d31b1b4514fc3ec007e23a7ba8139ea7607bcb91"

client = boto3.client(
    "s3",
    endpoint_url=ENDPOINT,
    aws_access_key_id=ACCESS_KEY,
    aws_secret_access_key=SECRET_KEY,
    config=Config(signature_version="s3v4", connect_timeout=30, read_timeout=300),
    region_name="auto",
)


def clean_filename(title: str, video_id: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in title)
    safe = safe[:80].strip("_")
    return f"{video_id}_{safe}.mp3"


def upload_to_r2(local_path: Path, key: str) -> str:
    print(f"    Uploading {key[:70]}...")
    client.upload_file(
        str(local_path), BUCKET, key,
        ExtraArgs={"ContentType": "audio/mpeg"},
    )
    return f"{PUB_BASE}/{key}"


def download_audio(video_id: str, out_dir: Path) -> Path | None:
    url = f"https://www.youtube.com/watch?v={video_id}"
    out_tmpl = str(out_dir / f"{video_id}.%(ext)s")
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "-x", "--audio-format", "mp3", "--audio-quality", "128K",
        "--no-playlist", "--no-warnings",
        "-o", out_tmpl,
        url,
    ]
    # Use cookies if available
    cookies = os.environ.get("YOUTUBE_COOKIES_FILE") or \
              str(Path.home() / ".config/yt-dlp/cookies.txt")
    if Path(cookies).exists():
        cmd += ["--cookies", cookies]

    result = subprocess.run(cmd, capture_output=False, timeout=300)
    if result.returncode != 0:
        return None
    for p in out_dir.glob(f"{video_id}.*"):
        if p.suffix == ".mp3":
            return p
    return None


def main():
    # Collect all broken entries across all feeds
    broken = []  # list of (feed_path, entry_index, entry)
    for feed_path in sorted(FEEDS_DIR.glob("*.entries.json")):
        entries = json.loads(feed_path.read_text(encoding="utf-8"))
        for i, e in enumerate(entries):
            if " " in e.get("audio_url", ""):
                broken.append((feed_path, i, e))

    print(f"Found {len(broken)} broken entries.\n")
    if not broken:
        print("Nothing to fix.")
        return

    fixed = 0
    failed = []

    for feed_path, idx, entry in broken:
        vid   = entry["video_id"]
        title = entry["title"]
        print(f"[{vid}] {title[:60]}")

        # Check if already uploaded with clean name
        clean_key = clean_filename(title, vid)
        try:
            client.head_object(Bucket=BUCKET, Key=clean_key)
            new_url = f"{PUB_BASE}/{clean_key}"
            print(f"  Already in R2 as {clean_key[:60]} — skipping download.")
        except client.exceptions.ClientError:
            # Need to download
            work_dir = AUDIO_DIR / vid
            work_dir.mkdir(exist_ok=True)
            print(f"  Downloading from YouTube...")
            mp3 = download_audio(vid, work_dir)
            if not mp3:
                print(f"  FAILED to download — skipping.")
                failed.append(vid)
                continue
            mp3.rename(work_dir / clean_key)
            new_url = upload_to_r2(work_dir / clean_key, clean_key)
            shutil.rmtree(work_dir, ignore_errors=True)

        # Update entries.json in memory
        entries = json.loads(feed_path.read_text(encoding="utf-8"))
        entries[idx]["audio_url"] = new_url
        feed_path.write_text(
            json.dumps(entries, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"  Fixed -> {new_url[:80]}")
        fixed += 1

    print(f"\nDone: {fixed} fixed, {len(failed)} failed.")
    if failed:
        print("Failed IDs:", failed)

    shutil.rmtree(AUDIO_DIR, ignore_errors=True)


if __name__ == "__main__":
    main()
