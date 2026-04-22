"""
tag_episodes.py
---------------
One-shot script to tag all existing episodes with themes using Claude Haiku.
Safe to re-run: skips already-tagged episodes.

Usage:
    ANTHROPIC_API_KEY=... python scripts/tag_episodes.py
"""

import json
import os
import re
import sys
import time
from pathlib import Path

import anthropic

THEMES = [
    "Chabbat",
    "Tefila",
    "Téchouva",
    "Emouna",
    "Etude de Torah",
    "Moussar",
    "Halakha",
    "Kabbala & Spiritualité",
    "Mariage & Famille",
    "Daf Hayomi",
    "Likoutei Moharan",
    "Roch Hachana & Yom Kippour",
    "Souccot & Sim'hat Torah",
    "Hanoucca",
    "Pourim",
    "Pessa'h",
    "Chavouot",
    "Paracha",
    "Histoire juive",
    "Am Israël & Actualité",
    "Santé & Réfoua",
    "Parnassa",
]

THEMES_STR = ", ".join(THEMES)
BATCH_SIZE  = 25
FEEDS_DIR   = Path("feeds")

SYSTEM_PROMPT = f"""You tag Torah podcast episode titles with themes.
Available themes (use ONLY these exact strings): {THEMES_STR}

Rules:
- Assign 1 to 3 themes per episode, only clear matches
- Titles can be in French, Hebrew, or both — understand both languages
- "Paracha" theme = episode is primarily about a weekly Torah portion
- Return ONLY a valid JSON array of arrays, no explanation, no markdown
"""


def tag_batch(client: anthropic.Anthropic, titles: list[str]) -> list[list[str]]:
    numbered = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(titles))
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"Tag these {len(titles)} episodes:\n{numbered}"}],
    )
    text = response.content[0].text.strip()
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        return [[] for _ in titles]
    try:
        result = json.loads(match.group())
        # Ensure valid themes only
        return [
            [t for t in (row if isinstance(row, list) else []) if t in THEMES]
            for row in result
        ]
    except json.JSONDecodeError:
        return [[] for _ in titles]


def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)
    total_tagged = 0

    for entries_file in sorted(FEEDS_DIR.glob("*.entries.json")):
        entries = json.loads(entries_file.read_text(encoding="utf-8"))
        untagged_idx = [i for i, e in enumerate(entries) if "tags" not in e]

        if not untagged_idx:
            print(f"{entries_file.stem}: already fully tagged ({len(entries)} episodes)")
            continue

        print(f"{entries_file.stem}: tagging {len(untagged_idx)}/{len(entries)} episodes...")

        for batch_start in range(0, len(untagged_idx), BATCH_SIZE):
            batch = untagged_idx[batch_start : batch_start + BATCH_SIZE]
            titles = [entries[i]["title"] for i in batch]
            tags   = tag_batch(client, titles)
            for j, idx in enumerate(batch):
                entries[idx]["tags"] = tags[j] if j < len(tags) else []
            print(f"  [{batch_start + len(batch)}/{len(untagged_idx)}] done")
            time.sleep(0.5)

        entries_file.write_text(
            json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        total_tagged += len(untagged_idx)

    print(f"\nDone. Total tagged: {total_tagged} episodes.")


if __name__ == "__main__":
    main()
