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
