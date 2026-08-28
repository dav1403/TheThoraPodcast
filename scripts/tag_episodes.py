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

from feeds_util import channel_entry_files

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

# Flag file read by the CI "Alert if Anthropic credits exhausted" step. Same
# pattern as process_podcasts.py's /tmp/auth_error: the Python script only drops
# a flag, the workflow turns it into a GitHub issue -> owner email. Kept so a
# credit/quota exhaustion no longer fails silently (was swallowed by the
# `|| echo "WARNING..."` on the tagging step).
CREDIT_ERROR_FLAG = Path("/tmp/anthropic_credit_error")

_CREDIT_KEYWORDS = ("credit", "quota", "insufficient", "billing", "payment", "balance")


def _is_credit_error(exc: Exception) -> bool:
    """True when the Anthropic API rejects the call for billing reasons
    (exhausted credits / insufficient quota) rather than a transient issue.

    Anthropic returns HTTP 400 ("Your credit balance is too low..."), and other
    providers/paths surface 402 or 429 insufficient_quota — we match those
    statuses only when the message mentions a billing keyword, so plain
    transient rate-limits (429 without a credit message) are not misreported."""
    msg = str(getattr(exc, "message", "") or exc).lower()
    if "insufficient_quota" in msg:
        return True
    status = getattr(exc, "status_code", None)
    return status in (400, 402, 429) and any(k in msg for k in _CREDIT_KEYWORDS)


def _flag_credit_error(exc: Exception) -> None:
    """Best-effort: drop the flag file the CI alert step turns into an email.
    Never raises — an alerting failure must not crash the tagging run."""
    try:
        CREDIT_ERROR_FLAG.write_text(str(exc))
    except Exception as flag_err:  # noqa: BLE001 - best effort, never fatal
        print(f"WARNING: could not write credit-error flag: {flag_err}")

SYSTEM_PROMPT = f"""You tag Torah podcast episode titles with themes.
Available themes (use ONLY these exact strings): {THEMES_STR}

Rules:
- Assign 1 to 3 themes per episode, only clear matches
- Titles can be in French, Hebrew, or both — understand both languages
- "Paracha" theme = episode is primarily about a weekly Torah portion
- "Daf Hayomi" ONLY applies to episodes that follow the standard Daf HaYomi cycle (daily Talmud Bavli / Gemara page). Do NOT apply it to other daily learning programs such as האוצר היומי, סדר ר"מ, Mishnah Yomit, Halacha Yomit, or any other daily schedule. When in doubt, do not use this tag.
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

    # Channel feeds only: speaker feeds are copies, tagging them would spend
    # Claude credits twice on the same episodes (see scripts/feeds_util.py).
    for entries_file in channel_entry_files(FEEDS_DIR):
        entries = json.loads(entries_file.read_text(encoding="utf-8"))
        untagged_idx = [i for i, e in enumerate(entries) if "tags" not in e]

        if not untagged_idx:
            print(f"{entries_file.stem}: already fully tagged ({len(entries)} episodes)")
            continue

        print(f"{entries_file.stem}: tagging {len(untagged_idx)}/{len(entries)} episodes...")

        for batch_start in range(0, len(untagged_idx), BATCH_SIZE):
            batch = untagged_idx[batch_start : batch_start + BATCH_SIZE]
            titles = [entries[i]["title"] for i in batch]
            try:
                tags = tag_batch(client, titles)
            except anthropic.APIStatusError as e:
                if _is_credit_error(e):
                    # Credits/quota exhausted: every further call will fail too.
                    # Flag it for the CI email alert, persist what we already
                    # tagged, and stop cleanly (exit 0) instead of failing
                    # silently under the workflow's `|| echo "WARNING..."`.
                    _flag_credit_error(e)
                    print(
                        "ERROR: Anthropic credits/quota exhausted — stopping "
                        f"tagging (alert flag written for CI). {e}"
                    )
                    entries_file.write_text(
                        json.dumps(entries, indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )
                    print(f"\nStopped early. Total tagged before stop: {total_tagged} episodes.")
                    return
                raise
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
