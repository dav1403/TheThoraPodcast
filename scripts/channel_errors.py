"""
channel_errors.py
-----------------
Per-channel error ledger of the "Update Podcast Feeds" workflow.

Why: on 21-22/10/2026 one channel (rav-meir-david-shmueli) raised in
process_podcasts.py, the script exited 1, and GitHub skipped every later step
of the job — backfill, pages, the R2 state push, the snapshot. No channel was
published for ~6 h because of a single one.

Now process_podcasts.py isolates a failing channel: it records the error here
and exits 0, so the other channels are processed, the state is pushed to R2
and the site is rebuilt. The failure is NOT hidden: the last step of the job
(`if: always()`) runs `python channel_errors.py check`, which prints one
GitHub error annotation per channel, writes a table to the step summary and
exits 1 when the ledger is not empty — the run ends red AFTER publication.

Stdlib only (the tests workflow does not install the ingest runtime).

Ledger: JSON lines, one object per error: {"step", "slug", "error"}.
Path: $TTP_CHANNEL_ERRORS_FILE, default /tmp/channel_errors.jsonl (same place
as the other run-scoped markers: /tmp/backfill_budget, /tmp/auth_error).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Iterable, TextIO

ENV_VAR = "TTP_CHANNEL_ERRORS_FILE"
DEFAULT_PATH = "/tmp/channel_errors.jsonl"
# One annotation / summary row must stay readable: tracebacks can be huge.
MAX_ERROR_CHARS = 1000


def ledger_path(path: str | os.PathLike | None = None) -> Path:
    if path is not None:
        return Path(path)
    return Path(os.environ.get(ENV_VAR) or DEFAULT_PATH)


def reset(path: str | os.PathLike | None = None) -> None:
    """Start a run with an empty ledger (a stale file must never turn a run red)."""
    p = ledger_path(path)
    try:
        p.unlink()
    except FileNotFoundError:
        pass


def record(errors: Iterable[tuple[str, str]], step: str,
           path: str | os.PathLike | None = None) -> int:
    """Append (slug, error) pairs to the ledger. Returns the number written."""
    p = ledger_path(path)
    rows = [{"step": step, "slug": str(slug), "error": str(err)[:MAX_ERROR_CHARS]}
            for slug, err in errors]
    if not rows:
        return 0
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(rows)


def load(path: str | os.PathLike | None = None) -> list[dict]:
    """Read the ledger. Missing file = no error. A corrupt line is kept as an
    error of its own: an unreadable ledger must not pass for a clean run."""
    p = ledger_path(path)
    if not p.exists():
        return []
    out: list[dict] = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("not an object")
        except ValueError:
            row = {"step": "?", "slug": "?", "error": f"unreadable ledger line: {line[:200]}"}
        out.append({
            "step": str(row.get("step", "?")),
            "slug": str(row.get("slug", "?")),
            "error": str(row.get("error", "")),
        })
    return out


def _escape_annotation(text: str) -> str:
    """GitHub workflow-command escaping of the message part."""
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _escape_property(text: str) -> str:
    return _escape_annotation(text).replace(":", "%3A").replace(",", "%2C")


def _md_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def check(path: str | os.PathLike | None = None, out: TextIO | None = None,
          summary_path: str | os.PathLike | None = None) -> int:
    """Report the ledger. Returns 0 when empty, 1 otherwise (caller exits with it)."""
    out = out or sys.stdout
    errors = load(path)
    if not errors:
        print("No channel error this run.", file=out)
        return 0

    slugs = sorted({e["slug"] for e in errors})
    print(f"{len(errors)} channel error(s) this run ({len(slugs)} channel(s)): "
          f"{', '.join(slugs)}", file=out)
    print("The other channels were published (state pushed to R2, site rebuilt); "
          "this step turns the run red so the failure stays visible.", file=out)
    for e in errors:
        title = _escape_property(f"Channel error: {e['slug']} ({e['step']})")
        print(f"::error title={title}::{_escape_annotation(e['slug'] + ': ' + e['error'])}",
              file=out)

    if summary_path is None:
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY") or None
    if summary_path:
        lines = [
            "",
            f"## :x: {len(errors)} channel error(s) — other channels published",
            "",
            "| Step | Channel | Error |",
            "|---|---|---|",
        ]
        lines += [f"| {_md_cell(e['step'])} | `{_md_cell(e['slug'])}` | {_md_cell(e['error'])} |"
                  for e in errors]
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    return 1


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["check"]:
        return check()
    if argv[:1] == ["reset"]:
        reset()
        return 0
    print("usage: channel_errors.py check|reset", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
