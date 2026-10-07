"""Phase 4 cutover: stop TRACKING the state and the generated artefacts in git.

The list is derived from scripts/site_state.py (STATE_DIRS, STATE_FILES,
GENERATED_ROOT_FILES, mobile/*.json, channel/speaker slugs) — the same single
source of truth that deploy-site and the pipeline use for their source-only
checkouts, so what leaves git is exactly what they never read from git.
Kept tracked on purpose: social_state.json, paracha/, paracha-de-la-semaine.*,
mobile/package.json, every hand-written page.

Usage (in a checkout of main AFTER the phase 4 merge, sparse is fine: the
index holds every entry):

  python scripts/phase4_untrack.py --dry-run      # prints the counts only
  python scripts/phase4_untrack.py                # git rm -r --cached + .gitignore
  git add --sparse .gitignore                     # --sparse: outside a sparse cone
  git diff --staged --name-only | wc -l           # == expected + 1 (.gitignore)
  git commit -m "phase4: stop tracking generated artefacts and state"
                         ^^^ NO pathspec: `git commit -- <paths>` commits the
                         working tree for those paths and silently UNDOES a
                         `git rm --cached` (verified 09/09/2026).
  git diff-tree -r --name-status HEAD~1 HEAD | grep -c '^D'   # == expected
  git diff-tree -r --name-status HEAD~1 HEAD | grep -v '^D'   # only .gitignore

Nothing is deleted from disk and nothing from history (phase 5 does that).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import site_state  # noqa: E402

MARK = "# --- phase 4: state lives in R2, generated pages are built by deploy-site ---"


def untracked_rules(slugs: list[str]) -> list[str]:
    """gitignore rules == the exclusions of site_state.sparse_patterns()."""
    rules = [f"/{d}/" for d in site_state.STATE_DIRS]
    rules += [f"/{f}" for f in site_state.STATE_FILES]
    rules += [f"/{f}" for f in site_state.GENERATED_ROOT_FILES]
    rules.append("/mobile/*.json")
    rules += [f"!/mobile/{f}" for f in site_state.MOBILE_SOURCE_JSON]
    for slug in slugs:
        for s in dict.fromkeys((slug, slug.lower())):
            rules += [f"/{s}.html", f"/{s}/"]
    return rules


def is_untracked_path(rel: str, slugs: list[str]) -> bool:
    if site_state.is_state_path(rel) or rel in site_state.GENERATED_ROOT_FILES:
        return True
    if rel.startswith("mobile/") and rel.endswith(".json") and rel.count("/") == 1:
        return rel.split("/", 1)[1] not in site_state.MOBILE_SOURCE_JSON
    names = {s for slug in slugs for s in (slug, slug.lower())}
    if "/" not in rel:
        return rel.endswith(".html") and rel[:-5] in names
    return rel.split("/", 1)[0] in names


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=".")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    repo = Path(args.repo)

    def git(*a: str, inp: bytes | None = None) -> bytes:
        return subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True, input=inp).stdout

    def blob(path: str) -> list[dict]:
        return json.loads(git("show", f":{path}").decode("utf-8-sig"))

    slugs = site_state.slugs_from(blob("channels.json"), blob("speakers.json"))
    tracked = [p for p in git("ls-files", "-z").decode("utf-8", "surrogateescape").split("\0") if p]
    doomed = [p for p in tracked if is_untracked_path(p, slugs)]
    by_top: dict[str, int] = {}
    for p in doomed:
        k = p.split("/", 1)[0] if "/" in p else "(root)"
        by_top[k] = by_top.get(k, 0) + 1
    print(f"tracked files: {len(tracked)}; to untrack: {len(doomed)}; kept: {len(tracked) - len(doomed)}")
    for k, n in sorted(by_top.items(), key=lambda kv: -kv[1])[:15]:
        print(f"  {n:6d}  {k}")
    for must_keep in ("channels.json", "speakers.json", "social_state.json", "index.html", "scripts/site_state.py"):
        if must_keep in doomed:
            print(f"::error::{must_keep} would be untracked: abort")
            return 2
    if args.dry_run:
        print(f"EXPECTED_DELETIONS={len(doomed)}")
        return 0

    # --sparse: in a sparse worktree most of these paths are outside the
    # sparse definition, and `git rm` refuses them without it.
    git("rm", "-r", "--cached", "--sparse", "--quiet", "--pathspec-from-file=-", "--pathspec-file-nul",
        inp="\0".join(doomed).encode("utf-8", "surrogateescape"))
    gi = repo / ".gitignore"
    # In a sparse worktree .gitignore may be absent from disk: start from the
    # committed one, never from empty (the 07/10 rehearsal caught exactly that:
    # the existing rules would have been replaced by the phase 4 block alone).
    if gi.exists():
        text = gi.read_text(encoding="utf-8")
    else:
        try:
            text = git("show", ":.gitignore").decode("utf-8")
        except subprocess.CalledProcessError:
            text = ""
    if MARK not in text:
        text = text.rstrip("\n") + "\n\n" + MARK + "\n" + "\n".join(untracked_rules(slugs)) + "\n"
        gi.write_text(text, encoding="utf-8", newline="\n")
    staged = [p for p in git("diff", "--staged", "--name-only", "-z").decode("utf-8", "surrogateescape").split("\0") if p]
    print(f"staged now: {len(staged)} path(s) (expected {len(doomed)} deletions; then `git add --sparse .gitignore`)")
    print(f"EXPECTED_DELETIONS={len(doomed)}")
    return 0 if len(staged) == len(doomed) else 1


if __name__ == "__main__":
    sys.exit(main())
