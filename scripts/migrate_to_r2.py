"""
migrate_to_r2.py
----------------
One-time migration script: moves all existing MP3s from GitHub Releases to R2
and updates the entries.json files so RSS feeds point to the new URLs.

Run locally (or as a one-off GitHub Actions job):
    export GITHUB_TOKEN=your_token
    export GITHUB_REPO=dav1403/TheThoraPodcast
    export R2_ENDPOINT_URL=https://<account_id>.r2.cloudflarestorage.com
    export R2_ACCESS_KEY_ID=your_r2_key
    export R2_SECRET_ACCESS_KEY=your_r2_secret
    export R2_BUCKET_NAME=your_bucket
    export R2_PUBLIC_URL=https://your-public-url.r2.dev
    python scripts/migrate_to_r2.py [--dry-run]
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_REPO  = os.environ.get("GITHUB_REPO", "")

if not GITHUB_TOKEN or not GITHUB_REPO:
    print("ERROR: GITHUB_TOKEN and GITHUB_REPO must be set.")
    sys.exit(1)

FEEDS_DIR = Path("feeds")


def gh_headers():
    return {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}


def get_r2_client():
    return boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT_URL"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )


def file_exists_in_r2(client, bucket: str, key: str) -> bool:
    try:
        client.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError:
        return False


def get_release_assets(slug: str) -> list[dict]:
    tag = f"audio-{slug}"
    r = requests.get(
        f"https://api.github.com/repos/{GITHUB_REPO}/releases/tags/{tag}",
        headers=gh_headers(),
    )
    if r.status_code == 404:
        return []
    r.raise_for_status()
    return r.json().get("assets", [])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Preview without uploading")
    args = parser.parse_args()

    channels = json.loads(Path("channels.json").read_text())
    bucket   = os.environ["R2_BUCKET_NAME"]
    pub_base = os.environ["R2_PUBLIC_URL"].rstrip("/")
    client   = get_r2_client()

    total_migrated = 0
    total_skipped  = 0
    total_updated  = 0

    for ch in channels:
        if not ch.get("enabled", True):
            continue
        slug = ch["slug"]
        print(f"\n{'='*60}")
        print(f"Channel: {slug}")

        assets = get_release_assets(slug)
        if not assets:
            print(f"  No GitHub Release assets found.")
            continue
        print(f"  Found {len(assets)} assets in GitHub Releases.")

        entries_path = FEEDS_DIR / f"{slug}.entries.json"
        if not entries_path.exists():
            print(f"  No entries file found, skipping.")
            continue
        entries = json.loads(entries_path.read_text())

        # Build a lookup: filename → r2_url (after migration)
        filename_to_r2 = {}

        for asset in assets:
            filename    = asset["name"]
            release_url = asset["browser_download_url"]
            r2_url      = f"{pub_base}/{filename}"

            if file_exists_in_r2(client, bucket, filename):
                print(f"  [skip] Already in R2: {filename}")
                filename_to_r2[filename] = r2_url
                total_skipped += 1
                continue

            if args.dry_run:
                print(f"  [dry-run] Would migrate: {filename}")
                filename_to_r2[filename] = r2_url
                continue

            print(f"  Downloading: {filename}")
            resp = requests.get(release_url, stream=True, timeout=300,
                                headers=gh_headers())
            resp.raise_for_status()

            tmp_path = Path(f"/tmp/{filename}")
            with open(tmp_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    f.write(chunk)

            print(f"  Uploading to R2: {filename}")
            with open(tmp_path, "rb") as f:
                client.put_object(
                    Bucket=bucket,
                    Key=filename,
                    Body=f,
                    ContentType="audio/mpeg",
                )
            tmp_path.unlink()
            filename_to_r2[filename] = r2_url
            total_migrated += 1
            print(f"  Migrated -> {r2_url}")
            time.sleep(0.5)

        # Update entries.json to point to R2 URLs
        updated = False
        for entry in entries:
            old_url = entry.get("audio_url", "")
            if "github.com" not in old_url:
                continue
            # Extract filename from GitHub Release URL
            filename = old_url.split("/")[-1]
            new_url  = filename_to_r2.get(filename)
            if new_url and new_url != old_url:
                if not args.dry_run:
                    entry["audio_url"] = new_url
                print(f"  Updated entry URL: {filename}")
                updated = True
                total_updated += 1

        if updated and not args.dry_run:
            entries_path.write_text(json.dumps(entries, indent=2, ensure_ascii=False))
            print(f"  Saved updated entries.json")

    print(f"\n{'='*60}")
    print(f"Migration complete:")
    print(f"  Migrated : {total_migrated} files")
    print(f"  Skipped  : {total_skipped} (already in R2)")
    print(f"  Updated  : {total_updated} entry URLs")
    if args.dry_run:
        print("  (dry-run — no changes written)")
    else:
        print("\nNext: run the workflow to regenerate RSS XML files from updated entries.")


if __name__ == "__main__":
    main()
