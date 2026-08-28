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
from pathlib import Path

SPEAKERS_FILE = "speakers.json"


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
