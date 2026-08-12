"""Regenerate the `slug` dropdown options in the "Update Channel Platforms"
workflow (.github/workflows/update_channel.yml) from channels.json.

Only channels that still have at least one MISSING platform among
spotify / apple / deezer (rss is ignored, it is always present) are listed,
so the dropdown only offers channels that actually need completing.

The options block is delimited by the markers BEGIN-AUTOGEN-SLUGS /
END-AUTOGEN-SLUGS so this can be re-run safely. If no channel is missing a
platform, a single placeholder option is written; the workflow itself then
exits 0 without changing anything when that placeholder is submitted.

Run from the repo root:  python scripts/refresh_update_channel_dropdown.py
Exit code 0 = success (whether or not the file changed).
"""
import json
import re
import sys
from pathlib import Path

PLACEHOLDER = "(aucune chaine a completer)"
BEGIN = "# BEGIN-AUTOGEN-SLUGS"
END = "# END-AUTOGEN-SLUGS"
# Indentation of the `options:` list items in the YAML (10 spaces).
OPT_INDENT = " " * 10
MARKER_INDENT = " " * 8

REPO_ROOT = Path(__file__).resolve().parent.parent
CHANNELS = REPO_ROOT / "channels.json"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "update_channel.yml"


def compute_missing_slugs() -> list[str]:
    channels = json.loads(CHANNELS.read_text(encoding="utf-8-sig"))
    missing = []
    for ch in channels:
        slug = ch.get("slug", "")
        if not slug:
            continue
        plats = ch.get("platforms", {}) or {}
        if any(not (plats.get(k) or "").strip() for k in ("spotify", "apple", "deezer")):
            missing.append(slug)
    return sorted(missing)


def build_block(slugs: list[str]) -> str:
    values = slugs if slugs else [PLACEHOLDER]
    lines = [f"{MARKER_INDENT}{BEGIN}"]
    for s in values:
        lines.append(f'{OPT_INDENT}- "{s}"')
    lines.append(f"{MARKER_INDENT}{END}")
    return "\n".join(lines)


def main() -> int:
    slugs = compute_missing_slugs()
    block = build_block(slugs)

    content = WORKFLOW.read_text(encoding="utf-8")
    pattern = re.compile(
        re.escape(MARKER_INDENT + BEGIN) + r".*?" + re.escape(MARKER_INDENT + END),
        re.DOTALL,
    )
    if not pattern.search(content):
        print("ERROR: marqueurs BEGIN/END-AUTOGEN-SLUGS introuvables dans le workflow")
        return 1

    new_content = pattern.sub(block, content)
    if new_content == content:
        print(f"No change ({len(slugs)} slug(s) manquant(s)).")
        return 0

    WORKFLOW.write_text(new_content, encoding="utf-8")
    print(f"Dropdown regenerated: {len(slugs)} slug(s) manquant(s).")
    for s in slugs:
        print(" -", s)
    if not slugs:
        print(f" (placeholder: {PLACEHOLDER})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
