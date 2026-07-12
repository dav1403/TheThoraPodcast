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
    "enabled": true
  }

> Run workflow

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

The generator suite itself is stdlib-only; `yt-dlp` is only needed by the
pre-existing `tests/test_youtube_discovery.py`. CI runs on `push` and
`pull_request` via `.github/workflows/tests.yml` (Python 3.12).

