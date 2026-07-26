# My Podcast
https://dav1403.github.io/TheThoraPodcast/links.html

## Setup (after cloning)

```bash
git config core.hooksPath .githooks
```

This activates the pre-commit hook that blocks accidental commits of empty or corrupted files.

## To add more Shows

> Edit channels.json

  {
    "slug": "Rabbi-Name",
    "youtube_channel_id": "UCxxxxxxxxxxxxxxxxxxxxxxx",
    "podcast_author": "Rabbi Name",
    "podcast_email": "email",
    "podcast_language": "fr",
    "podcast_category": "Religion & Spirituality",
    "enabled": true,
    "intro_trim_sec": 0
  }

> Run workflow

## Cutting an intro jingle (copyright takedowns)

Some channels open every episode with a musical **jingle** that triggers
copyright takedowns (podcasts get pulled from Spotify). To strip the leading
seconds of audio, set **`intro_trim_sec`** on that channel in `channels.json`:

- `0` (default) — no trimming, pipeline byte-identical to before.
- `> 0` — cut the first N seconds off every episode of that channel. The value
  is per channel (jingle length varies by rabbi/channel), e.g. `"intro_trim_sec": 12`.

Applied via an `ffmpeg -ss <sec>` input seek during conversion; NEW episodes are
trimmed automatically by `process_podcasts.py` / `backfill_podcasts.py`.

**Retro-trim episodes already live on R2** with `scripts/reprocess_trim.py`
(dry-run by default — nothing is written until you pass `--apply`):

    # preview what would be re-trimmed for one channel:
    py scripts/reprocess_trim.py --channel <slug>

    # actually download → trim → overwrite the R2 objects in place:
    py scripts/reprocess_trim.py --channel <slug> --apply

It is idempotent (each entry is stamped `intro_trimmed`, so re-running never
double-trims) and updates the feed sidecars + rebuilds the RSS.

## Tests

An automated pytest suite guards `scripts/generate_channel_pages.py` — the
generator that produces the 13k static pages, `home.json` and
`search-index.json` — so a regression is caught in CI **before** it reaches the
live site (the "generator trap").

The tests are fast: they exercise the generator's functions against tiny
fixtures in `tests/fixtures/` (2 test channels + 1 speaker) and never
regenerate the real catalog. What is covered:

- `build_home_json` — 20 most-recent episodes, sorted desc, HITAT excluded from
  the recents row, speaker/channel structure.
- `build_search_index` — required fields (`t,c,u,d`), whole-catalog coverage
  (HITAT included), non-empty URLs matching `ep_path`.
- `render_page` / `render_speaker_page` — no leftover `{{ }}` placeholders,
  `<title>` / `lang` / `canonical` present, audio-button count == episode count.
- Committed-artifact integrity — `home.json` / `search-index.json` are valid
  JSON and a sample of their URLs resolve to real `.html` files in the repo.
- End-to-end — running the whole generator over the fixtures emits clean,
  well-formed pages.

### Run locally

```bash
python -m pytest tests/ -q
```

On this Windows machine, use the `py` launcher (plain `python` may hit the
Microsoft Store stub):

```bash
py -m pip install pytest yt-dlp   # one-time
py -m pytest tests/ -q
```

The generator suite itself is stdlib-only (only `pytest` required). `yt-dlp`
above is only needed if you also want to run the pre-existing
`tests/test_youtube_discovery.py` locally — CI does not run that one (it pulls
the full YouTube-ingest runtime). CI runs the generator suite on `push` and
`pull_request` via `.github/workflows/tests.yml` (Python 3.12).

