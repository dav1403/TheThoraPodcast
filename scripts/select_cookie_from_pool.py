#!/usr/bin/env python3
"""Select one YouTube cookie from a rotating pool and write it to a file.

Used by the CI "Write YouTube cookies" step to spread download load across a
pool of many YouTube accounts (David owns lots of them). Rotating the cookie
per run makes each account look like an occasional human rather than a bot
hammering the API from one identity.

Inputs (all via environment variables):
  YOUTUBE_COOKIES_POOL     Multiple Netscape cookie files concatenated, each
                           separated by a line that is exactly the sentinel
                           "-----COOKIE-----" (see SEPARATOR below). A Netscape
                           cookie file itself contains tabs and newlines, which
                           is why a plain newline can NOT be used as a
                           separator — we need a sentinel line that never
                           appears inside a real cookie file.
  YOUTUBE_COOKIES          Single cookie file (legacy / fallback). Used when the
                           pool is absent, empty, or malformed. Full backward
                           compatibility with the pre-pool behaviour.
  COOKIE_ROTATION_INDEX    Integer used to pick which cookie to use, modulo the
                           pool size. In CI this is ${{ github.run_number }} so
                           each run advances one slot. Defaults to 0.
  COOKIE_ROTATION_OFFSET   Integer added to COOKIE_ROTATION_INDEX before the
                           modulo. Lets a second lane (e.g. the self-hosted
                           workflow) pick a *different* cookie than the hosted
                           lane at the same time slot. Defaults to 0.

Output:
  Writes the selected cookie to the path given by --output (default
  /tmp/yt_cookies.txt). If no usable cookie is found, writes nothing and exits
  0 (the caller only sets YOUTUBE_COOKIES_FILE when the file is non-empty, so a
  missing pool/secret degrades gracefully to "run without cookies").

Never prints cookie contents — only the chosen index / pool size / source.
Always exits 0 so a cookie problem never hard-fails the pipeline; problems are
surfaced as GitHub Actions ::warning:: annotations instead.
"""

import argparse
import os
import sys

SEPARATOR = "-----COOKIE-----"


def _warn(msg: str) -> None:
    """Emit a GitHub Actions warning annotation (and a plain line for local runs)."""
    print(f"::warning::{msg}")


def split_pool(raw: str) -> list[str]:
    """Split a concatenated pool into individual cookie files.

    Splits on lines whose stripped content equals SEPARATOR, then drops entries
    that are blank or contain only comments/whitespace (a valid Netscape file
    has at least one non-comment, non-empty line).
    """
    chunks: list[list[str]] = [[]]
    for line in raw.splitlines():
        if line.strip() == SEPARATOR:
            chunks.append([])
        else:
            chunks[-1].append(line)

    cookies: list[str] = []
    for chunk in chunks:
        text = "\n".join(chunk).strip()
        if not text:
            continue
        # Require at least one line that is not blank and not a comment.
        has_data = any(
            ln.strip() and not ln.lstrip().startswith("#")
            for ln in chunk
        )
        if has_data:
            cookies.append(text)
    return cookies


def _int_env(name: str, default: int = 0) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        _warn(f"{name} is not an integer; treating as {default}")
        return default


def select_cookie() -> str | None:
    """Return the chosen cookie file contents, or None if none configured."""
    pool_raw = os.environ.get("YOUTUBE_COOKIES_POOL", "") or ""
    single = os.environ.get("YOUTUBE_COOKIES", "") or ""

    if pool_raw.strip():
        cookies = split_pool(pool_raw)
        n = len(cookies)
        if n == 0:
            _warn(
                "YOUTUBE_COOKIES_POOL is set but no valid cookie could be parsed "
                f"(expected entries separated by a line '{SEPARATOR}'); "
                "falling back to YOUTUBE_COOKIES."
            )
        else:
            idx = (_int_env("COOKIE_ROTATION_INDEX") + _int_env("COOKIE_ROTATION_OFFSET")) % n
            print(f"Cookie pool: selected index {idx} of {n} cookie(s).")
            return cookies[idx]

    if single.strip():
        print("Cookie pool: not set/empty — using single YOUTUBE_COOKIES secret.")
        return single

    print("Cookie pool: no YOUTUBE_COOKIES_POOL and no YOUTUBE_COOKIES — running without cookies.")
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", default="/tmp/yt_cookies.txt",
                    help="Path to write the selected cookie file (default /tmp/yt_cookies.txt).")
    args = ap.parse_args()

    cookie = select_cookie()
    if cookie is None:
        return 0

    # Preserve the trailing newline yt-dlp's Netscape parser is happy with.
    with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(cookie.rstrip("\n") + "\n")
    print(f"Wrote selected cookie to {args.output}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
