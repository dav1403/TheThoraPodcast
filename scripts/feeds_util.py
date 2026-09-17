"""
feeds_util.py
-------------
Shared helpers for the scripts that walk feeds/*.entries.json.

The site publishes one derived feed per guest speaker: generate_channel_pages.py
copies the matching episodes out of the host channels into
feeds/<speaker>.entries.json so the speakers are reachable the same way the
channels are. A plain feeds/*.entries.json glob therefore no longer means "one
file per channel".

Speaker feeds are duplicates of episodes that already live in a channel feed, so
per-episode work (AI tagging, R2 repair, duration lookups) must skip them: it
would be paid for twice, and anything written there is overwritten on the next
generator run. Use channel_entry_files() instead of globbing directly.
"""
import json
import re
import uuid
from pathlib import Path

SPEAKERS_FILE = "speakers.json"

# Podcasting 2.0 namespace, declared on <rss> so <podcast:guid> resolves.
PODCAST_NS = "https://podcastindex.org/namespace/1.0"

# Namespace UUID fixed by the <podcast:guid> spec. Do not change it: it is what
# makes the identifier reproducible across every tool that reads the feed.
PODCAST_GUID_NAMESPACE = uuid.UUID("ead4c236-bf58-58c6-a2c6-a6b28d128cb6")

_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://")


def podcast_guid(feed_url: str) -> str:
    """The <podcast:guid> of a show, derived from its feed URL.

    The spec asks for a UUID v5 over the feed URL stripped of its protocol and
    of any trailing slash, so `https://example.com/feeds/x.xml` hashes as
    `example.com/feeds/x.xml`.

    This value must stay stable for the lifetime of a show: directories key
    their listing on it, so a GUID that moves creates the duplicate entry it
    exists to prevent. It is a pure function of the feed URL — never seed it
    from a random UUID, a hash of the episodes, or a YouTube channel id.
    """
    name = _SCHEME_RE.sub("", feed_url.strip()).rstrip("/")
    return str(uuid.uuid5(PODCAST_GUID_NAMESPACE, name))


def speaker_slugs(root: Path = Path(".")) -> set[str]:
    """Slugs of the guest speakers whose feeds are derived from channel feeds."""
    try:
        speakers = json.loads((Path(root) / SPEAKERS_FILE).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return set()
    if not isinstance(speakers, list):
        return set()
    return {sp["slug"] for sp in speakers if isinstance(sp, dict) and sp.get("slug")}


def entries_slug(path: Path) -> str:
    """feeds/lev.entries.json -> 'lev'."""
    return Path(path).stem.replace(".entries", "")


def channel_entry_files(feeds_dir: Path, root: Path = Path(".")) -> list[Path]:
    """Sorted feeds/*.entries.json for real channels — derived speaker feeds excluded."""
    derived = speaker_slugs(root)
    return sorted(
        f for f in Path(feeds_dir).glob("*.entries.json")
        if entries_slug(f) not in derived
    )
