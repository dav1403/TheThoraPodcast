"""
repair_missing_r2.py
--------------------
Fixes the 22 entries that have spaces in their R2 URLs and are missing from R2.
For each: downloads from GitHub Releases, uploads to R2 with underscores, updates entries.json.

Run via GitHub Actions (uses secrets) or locally with env vars set.
"""
import json, os, sys, time
from pathlib import Path
from urllib.parse import unquote
import requests
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
GITHUB_REPO  = os.environ.get("GITHUB_REPO", "dav1403/TheThoraPodcast")
BUCKET       = os.environ["R2_BUCKET_NAME"]
PUB_BASE     = os.environ["R2_PUBLIC_URL"].rstrip("/")
FEEDS_DIR    = Path("feeds")

def gh_headers():
    return {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}

def r2_client():
    return boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT_URL"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        config=Config(signature_version="s3v4", connect_timeout=30, read_timeout=300),
        region_name="auto",
    )

def get_release_assets(slug):
    r = requests.get(
        f"https://api.github.com/repos/{GITHUB_REPO}/releases/tags/audio-{slug}",
        headers=gh_headers(), timeout=15,
    )
    if r.status_code == 404:
        return {}
    r.raise_for_status()
    # map: filename_with_spaces → download_url
    return {a["name"]: a["browser_download_url"] for a in r.json().get("assets", [])}

def spaces_to_underscores(filename):
    return filename.replace(" ", "_")

def main():
    client = r2_client()

    slugs = [
        "rav-itshak-cohen", "rav-yonathan-mergui", "rav-barak-ben-nissan",
        "rav-yaakov-sitruk", "rav-ruben-attal", "rabbin-jonas",
    ]

    total_fixed = 0

    for slug in slugs:
        entries_path = FEEDS_DIR / f"{slug}.entries.json"
        if not entries_path.exists():
            continue
        entries = json.loads(entries_path.read_text())
        assets  = get_release_assets(slug)

        changed = False
        for entry in entries:
            url = entry.get("audio_url", "")
            if "r2.dev" not in url or " " not in url:
                continue

            # Derive the spaced filename from the broken URL
            spaced_filename = url.split("/")[-1]
            clean_filename  = spaces_to_underscores(spaced_filename)
            new_url         = f"{PUB_BASE}/{clean_filename}"

            # Check if already fixed in R2
            try:
                client.head_object(Bucket=BUCKET, Key=clean_filename)
                print(f"  [already ok] {clean_filename}")
                entry["audio_url"] = new_url
                changed = True
                continue
            except ClientError:
                pass

            # Find in GitHub Releases (try spaced name first, then unquoted)
            download_url = assets.get(spaced_filename) or assets.get(unquote(spaced_filename))
            if not download_url:
                print(f"  [not found in releases] {slug}: {entry['video_id']}")
                continue

            print(f"  Downloading: {spaced_filename[:60]}")
            tmp = Path(f"/tmp/{clean_filename}")
            with requests.get(download_url, stream=True, timeout=300, headers=gh_headers()) as r:
                r.raise_for_status()
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(65536):
                        f.write(chunk)

            print(f"  Uploading to R2: {clean_filename[:60]}")
            from boto3.s3.transfer import TransferConfig
            client.upload_file(
                str(tmp), BUCKET, clean_filename,
                ExtraArgs={"ContentType": "audio/mpeg"},
                Config=TransferConfig(multipart_threshold=8*1024*1024, max_concurrency=4),
            )
            tmp.unlink()

            entry["audio_url"] = new_url
            changed = True
            total_fixed += 1
            print(f"  Fixed -> {new_url}")
            time.sleep(0.5)

        if changed:
            entries_path.write_text(json.dumps(entries, indent=2, ensure_ascii=False))
            print(f"  Saved entries.json for {slug}")

    print(f"\nDone. Fixed {total_fixed} files.")

if __name__ == "__main__":
    main()
