# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**The Thora Podcast** is an automated pipeline that converts YouTube Torah lecture videos into podcast RSS feeds, hosted entirely on GitHub (Actions + Releases + Pages).

## Commands

```bash
# Install dependencies (requires Python 3.11+)
pip install -r requirements.txt

# Required environment variables
export YOUTUBE_API_KEY="..."
export GITHUB_TOKEN="..."
export GITHUB_REPO="owner/repo"

# Bootstrap a new channel (backfill historical episodes)
python scripts/bootstrap_channel.py --slug <channel-slug> --max <count>

# Process all enabled channels for new episodes
python scripts/process_podcasts.py
```

The project has no test suite or linter configured.

## Architecture

```
YouTube API → yt-dlp (audio extract) → GitHub Releases (MP3 storage) → RSS XML + JSON → GitHub Pages
```

**Data pipeline per episode:**
1. YouTube Data API v3 fetches latest videos per channel
2. yt-dlp downloads audio as 128kbps MP3
3. MP3 uploaded to GitHub Releases as a release asset (free CDN)
4. Video ID recorded in `processed.json` (deduplication)
5. Episode metadata appended to `<slug>.entries.json`
6. Full RSS feed regenerated as `<slug>.xml` using feedgen

**Key files:**
- `channels.json` — channel definitions (slug, YouTube channel ID, podcast metadata, enabled flag)
- `processed.json` — tracks processed video IDs per channel to avoid reprocessing
- `feeds/<slug>.xml` — generated RSS feeds consumed by podcast apps
- `feeds/<slug>.entries.json` — JSON sidecar with episode metadata, loaded by `index.html`
- `scripts/process_podcasts.py` — main worker, runs every 6 hours via GitHub Actions
- `scripts/bootstrap_channel.py` — one-time initialization for new channels
- `index.html` — client-side web UI that fetches `.entries.json` files and renders audio players

**Automation:** `.github/workflows/update_podcasts.yml` runs on a `0 */6 * * *` cron. It bootstraps any channel missing a feed (using `bootstrap_channel.py`), then runs `process_podcasts.py`, and commits/pushes the updated feeds.

**Hosting model:** GitHub Releases holds all MP3 files; GitHub Pages serves the static RSS XML, JSON, and HTML. No external storage or server is needed.

## Adding a New Channel

Add an entry to `channels.json` with `"enabled": true`. On the next GitHub Actions run, `bootstrap_channel.py` will detect the missing feed and backfill it automatically.
