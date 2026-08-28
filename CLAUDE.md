# CLAUDE.md

This file provides guidance to Claude Code when working with this repository.

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
export SPOTIFY_CLIENT_ID="..."
export SPOTIFY_CLIENT_SECRET="..."

# Process new episodes for all enabled channels (budget = max downloads this run)
python scripts/process_podcasts.py --budget 10

# Backfill historical episodes with remaining budget
python scripts/backfill_podcasts.py --budget 5

# Bootstrap a brand-new channel (backfill last N episodes)
python scripts/bootstrap_channel.py --slug <channel-slug> --max 10

# Backfill Apple / Deezer / Spotify platform episode links into entries JSON
python scripts/backfill_platform_links.py --lookback-days 365
python scripts/backfill_platform_links.py --channel <slug>  # single channel

# Regenerate all channel + episode HTML pages
python scripts/generate_channel_pages.py
```

No test suite or linter is configured. Scripts must be run from the repo root.


## Règles JavaScript — OBLIGATOIRES avant tout commit

### 1. utils.js est la source unique
Ces fonctions sont dans `js/utils.js` et ne doivent JAMAIS être redéclarées dans les pages HTML :
`escapeHtml`, `slugify`, `epUrl`, `formatDate`, `formatDuration`, `playIcon`, `pauseIcon`, `shareIcon`, `filterDurAll`, `setDur`

**Avant toute modif JS dans un fichier HTML :**
```bash
grep -n "function filterDurAll\|function escapeHtml\|function slugify" <fichier.html>
```
Si une de ces fonctions apparaît → la supprimer.

### 2. Vérification obligatoire après toute suppression de code JS
Après avoir supprimé un bloc de fonctions, vérifier qu'il ne reste pas de code orphelin :
```bash
node --check <fichier.html>  # ne marche pas directement, utiliser le hook
git add <fichier.html> && git commit  # le hook pre-commit vérifie automatiquement
```

### 3. Checklist avant commit de fichiers HTML avec JS
- [ ] `filterDurAll` pas redéclaré localement (sauf si c'est utils.js)
- [ ] `_activeDur` déclaré si utilisé
- [ ] Syntaxe JS valide (hook bloquera sinon)
- [ ] Fonctions requises présentes (voir REQUIRED_FUNCTIONS dans pre-commit-check.py)
- [ ] Tester dans un vrai navigateur après déploiement

### 4. Le hook pre-commit détecte automatiquement
- Fichiers vides / tronqués
- Erreurs de syntaxe JS
- Fonctions de utils.js redéclarées en doublon
- Fonctions requises manquantes
- Code orphelin (`_activeDur` non déclaré)

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
  generate_channel_pages.py
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

**Platform links backfill (`backfill_platform_links.py`):**
- Queries Apple iTunes API (public, no auth) and Deezer public API
- Spotify: uses Client Credentials OAuth (requires SPOTIFY_CLIENT_ID + SPOTIFY_CLIENT_SECRET)
- Fuzzy-matches episode titles (SequenceMatcher >= 0.70) against entries
- Writes episode-specific URLs into `platform_links` key of each entry
- Never overwrites existing URLs; logs all matches and misses
- Run with `--lookback-days 365` for full historical backfill

**Key data files:**
- `channels.json` — channel definitions; includes `platforms` (Spotify/Apple/Deezer show URLs)
- `processed.json` — set of video IDs already downloaded, keyed by channel slug
- `backfill_state.json` — per-channel backfill cursor (`page_token`, `exhausted` flag)
- `feeds/<slug>.entries.json` — ordered episode list; contains `platform_links` per episode
- `feeds/<slug>.xml` — generated RSS, served via GitHub Pages
- `speakers.json` — guest speakers (15), matched by title pattern inside a host channel's feed
- `feeds/<speaker>.entries.json` — **derived**, rewritten from the host channels by
  `generate_channel_pages.py` every run. Never edit by hand. It exists so anything that
  resolves a rav by slug (the mobile app in particular) reaches the 15 guests, not only the
  22 channels. Scripts that walk `feeds/*.entries.json` must go through
  `scripts/feeds_util.py::channel_entry_files()` — per-episode work (AI tagging, R2 repair,
  duration lookups) on these copies is paid for twice and overwritten on the next run.

**Static site generation (`generate_channel_pages.py`):**
- Generates `<slug>.html` (channel page) and `<slug>/<ep-slug>.html` (episode pages)
- Episode pages show Spotify / Apple Podcasts / Deezer links from `platform_links`
- Falls back to show-level platform URL when no episode-specific link exists
- All pages load shared utilities from `js/utils.js` (slugify, epUrl, escapeHtml, etc.)
- Run manually or after updating entries; CI does NOT auto-run this script

**GitHub Actions (`update_podcasts.yml`):**
- Runs every 3 hours on cron
- Installs ffmpeg (apt) and Deno (required by yt-dlp EJS n-challenge solver)
- Bootstraps any channel whose entries file is missing or empty
- Runs `process_podcasts.py --budget 10`, then `backfill_podcasts.py` with remaining budget
- Commits `feeds/`, `processed.json`, `backfill_state.json`, `artwork/` with `[skip ci]`
- Does NOT run `generate_channel_pages.py` or `backfill_platform_links.py` (manual)

**YouTube cookies:** The workflow reads `YOUTUBE_COOKIES` secret and writes it to `/tmp/yt_cookies.txt`.
A Raspberry Pi at `192.168.1.57` refreshes this secret every Monday at 08:00.

## Adding a New Channel

Add an entry to `channels.json` with `"enabled": true` and fill in the `platforms` object
(Spotify show URL, Apple Podcasts show URL, Deezer show URL, RSS URL). The next Actions run
will detect the missing feed and auto-bootstrap it with the last 10 episodes. Then run
`backfill_platform_links.py` and `generate_channel_pages.py` manually.

## Static Pages

The site has two types of pages:
- **Generated** by `generate_channel_pages.py`: `<slug>.html`, `<slug>/<ep>.html`, `sitemap.xml`
- **Static/manual**: `index.html`, `links.html`, `derniers-cours.html`, `parasha.html`,
  `themes.html`, `daf-hayomi.html`

All pages (static and generated) load `js/utils.js` for shared JS utilities. When modifying
shared functions (slugify, epUrl, etc.), edit `js/utils.js` only — never duplicate in pages.

## Local Dev Workflow (Windows)

This repo is developed on Windows but CI runs on Linux.

**Python** — PowerShell only, full path:
```
C:\Users\David\AppData\Local\Programs\Python\Python311\python.exe script.py
```
Never use `python` or `python3` in Bash (Microsoft Store alias, exit code 49).

**Git / file editing** — working copy at `C:\tmp\TTPclone` (PowerShell) = `/c/tmp/TTPclone` (Bash):
```bash
cd /c/tmp/TTPclone
git add <files> && git commit -m "..." && git pull --rebase origin main && git push origin main
```

**Never use `sed` or Bash redirections (`>`) to edit files with non-ASCII content** (Hebrew,
accented characters). Use Python `open().read().replace().write()` instead — `sed` silently
corrupts such files on Windows.

**Pre-commit hook** — `.githooks/pre-commit` blocks commits with empty files, truncated files
(>70% line loss vs HEAD), invalid HTML structure, or invalid JSON. Activate after cloning:
```bash
git config core.hooksPath .githooks
```

**UTF-8 BOM** — PowerShell `Set-Content -Encoding utf8` writes a BOM. Rules:
- Write JSON files from Bash or Python `open(..., 'wb').write(...encode('utf-8'))`
- All Python JSON reads use `encoding="utf-8-sig"` (strips BOM silently)

**GitHub Secrets required:**
- `YOUTUBE_API_KEY`, `GITHUB_TOKEN`, `R2_*` — CI pipeline
- `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET` — platform links backfill
- `YOUTUBE_COOKIES` — yt-dlp authentication

**After pushing a fix to CI scripts:**
```bash
gh workflow run update_podcasts.yml --repo dav1403/TheThoraPodcast
gh run list --repo dav1403/TheThoraPodcast --limit 3
```

**Platform title verification** — `scripts/verify_platform_titles.py` +
`.github/workflows/verify_platform_titles.yml` (weekly, Monday 06:00 UTC) check
that titles shown on Apple/Spotify still match the site feed (platforms serve
stale cached titles until they re-ingest the RSS). Join key = YouTube `video_id`
(= RSS `<guid>` = Apple `episodeGuid`). On mismatch it opens/updates a GitHub
issue (label `bug`) and fails the run; auto-closes on recovery. Run manually:
```bash
python3 scripts/verify_platform_titles.py            # local, exit 1 if mismatch
gh workflow run verify_platform_titles.yml --repo dav1403/TheThoraPodcast
```
- **Apple** needs no credentials (public iTunes Lookup API). The Apple podcast id
  is parsed from `channels.json` → `platforms.apple` (`.../id<NUMBER>`), or set an
  explicit `"apple_podcast_id"` on a channel. All current channels resolve an id.
- **Spotify** is skipped unless `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` are
  present (the repo already has these secrets); Apple coverage alone catches the
  reported problem, so Spotify support in the script is left as a documented TODO.

**Line endings** — `.gitattributes` forces LF (`* text=auto eol=lf`) so
`generate_channel_pages.py` produces byte-identical output on Windows and Linux.
Without it, regenerating on Windows rewrites every page with CRLF (huge spurious
diffs). Always regenerate with LF output.
