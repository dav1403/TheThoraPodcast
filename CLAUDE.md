# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies (Python 3.11+, plus ffmpeg and Deno must be on PATH)
pip install -r requirements.txt

# Required environment variables for local runs
export YOUTUBE_API_KEY="..."
export GITHUB_TOKEN="..."
export GITHUB_REPO="dav1403/TheThoraPodcast"
export R2_ENDPOINT_URL="..."
export R2_ACCESS_KEY_ID="..."
export R2_SECRET_ACCESS_KEY="..."
export R2_BUCKET_NAME="thetorahpodcast"
export R2_PUBLIC_URL="..."

# Process new episodes for all enabled channels (budget = max downloads this run)
python scripts/process_podcasts.py --budget 10

# Backfill historical episodes with remaining budget
python scripts/backfill_podcasts.py --budget 5

# Bootstrap a brand-new channel (backfill last N episodes)
python scripts/bootstrap_channel.py --slug <channel-slug> --max 10

# Repair local feeds (regenerate XML from entries.json without downloading)
python scripts/repair_local.py

# Repair MP3s missing from R2 (re-upload from local tmp_audio or re-download)
python scripts/repair_missing_r2.py
```

No test suite or linter is configured. Scripts must be run from the repo root.

## Architecture

```
YouTube Data API v3
       ↓
  yt-dlp + Deno (n-challenge solver)
       ↓
  Cloudflare R2  ←→  boto3 (S3-compatible)
       ↓
  feeds/<slug>.entries.json  +  feeds/<slug>.xml
       ↓
  GitHub Pages (static hosting)
```

**Per-episode pipeline (`process_podcasts.py`):**
1. YouTube Data API fetches the latest video IDs for each channel's uploads playlist
2. Video IDs are checked against `processed.json` to skip already-processed ones
3. Audio is downloaded as 128 kbps MP3 via yt-dlp into `tmp_audio/`
4. MP3 is uploaded to Cloudflare R2 via multipart transfer; the public URL is recorded
5. Episode metadata is appended to `feeds/<slug>.entries.json` (the source of truth)
6. The full RSS XML is regenerated from that JSON sidecar using feedgen

**Backfill strategy (`backfill_podcasts.py`):**
- Imports all helpers directly from `process_podcasts.py`
- Round-robins one slot per channel per run, walking backwards through the uploads playlist
- Tracks exhausted channels and pagination cursors in `backfill_state.json`

**Key data files:**
- `channels.json` — channel definitions; add `"enabled": true` to onboard a new channel
- `processed.json` — set of video IDs already downloaded, keyed by channel slug
- `backfill_state.json` — per-channel backfill cursor (`page_token`, `exhausted` flag)
- `feeds/<slug>.entries.json` — ordered episode list; all feed rebuilds read from this
- `feeds/<slug>.xml` — generated RSS, served via GitHub Pages

**GitHub Actions (`update_podcasts.yml`):**
- Runs every 3 hours on cron
- Installs ffmpeg (apt) and Deno (required by yt-dlp EJS n-challenge solver)
- Bootstraps any channel whose entries file is missing or empty
- Runs `process_podcasts.py --budget 10`, then `backfill_podcasts.py` with remaining budget
- Commits `feeds/`, `processed.json`, `backfill_state.json`, `artwork/` with `[skip ci]`

**YouTube cookies:** The workflow reads `YOUTUBE_COOKIES` secret and writes it to `/tmp/yt_cookies.txt`. A Raspberry Pi at `192.168.1.57` refreshes this secret every Monday at 08:00 by extracting cookies from its always-running Chromium session.

## Adding a New Channel

Add an entry to `channels.json` with `"enabled": true`. The next Actions run will detect the missing feed and auto-bootstrap it with the last 10 episodes.

## Local Dev Workflow (Windows)

This repo is developed on Windows but CI runs on Linux. Rules to avoid breakage:

**Python** — PowerShell only, full path:
```powershell
C:\Users\David\AppData\Local\Programs\Python\Python311\python.exe script.py
```
Never use `python` or `python3` in Bash (Microsoft Store alias, exit code 49).

**Git / file editing** — the working copy lives at `C:\tmp\TTPclone` (PowerShell) = `/c/tmp/TTPclone` (Bash). Always edit files there, then commit and push from Bash:
```bash
cd /c/tmp/TTPclone
git add <files>
git commit -m "..."
git push origin main
```

**Never use Bash heredocs for Python code** — single quotes in Python dict keys break shell parsing. Write Python files via PowerShell `Set-Content`, or use the Edit/Write tools directly on `C:\tmp\TTPclone\...`.

**UTF-8 BOM** — PowerShell `Set-Content -Encoding utf8` writes a UTF-8 BOM (`\xef\xbb\xbf`). On Linux CI, `json.loads()` crashes on it. Rules:
- Write JSON files from Bash: `printf '{}' > file.json` (never PowerShell for JSON)
- All Python JSON reads use `encoding="utf-8-sig"` (strips BOM silently, harmless without one)
- All Python JSON writes use `encoding="utf-8"` (explicit, no BOM)

**yt-dlp options** — always `quiet=True, no_warnings=True` in production. Verbose mode generates thousands of lines per download.

**After pushing a fix to CI scripts** — always trigger a manual run and verify the result before closing the task:
```bash
gh workflow run update_podcasts.yml --repo dav1403/TheThoraPodcast
gh run list --repo dav1403/TheThoraPodcast --limit 3
```
