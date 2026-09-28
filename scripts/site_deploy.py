"""Guards and checks around the Pages deployment (Actions mode).

Used by .github/workflows/deploy-site.yml (phase 3 of moving generated
artefacts out of git). The site is rebuilt from the source of `main` + the
state mirrored in R2; these commands decide whether a build may be published
and check what is served afterwards.

  freshness  compare with the live /_deploy.json: never publish an older
             state or source than the one online; skip exact re-deploys
  drift      is the R2 state the state of `main`? (tree diff, no blob read)
  pin/unpin  generate with the source files of the state commit, then put
             main's back (the committed artefacts were generated from those)
  content    rebuilt feeds vs `main`: same number of .xml feeds, no
             *.entries.json with fewer episodes
  marker     write /_deploy.json (run id + commits) into the site
  verify     poll the live marker, then probe the live site

Every command prints its verdict and, in Actions, writes key=value pairs to
GITHUB_OUTPUT.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

UA = {"User-Agent": "ttp-deploy-site/1.0"}
STATE_PATHS = ("feeds", "artwork", "search-fts", "processed.json", "backfill_state.json")


# --------------------------------------------------------------------------
# Pure helpers (unit-tested)
# --------------------------------------------------------------------------

def freshness_verdict(live: dict | None, main_sha: str, main_time: int,
                      state_commit: str, state_time: int, force: bool) -> tuple[bool, str]:
    """(deploy?, reason). `live` = the served /_deploy.json, None if absent."""
    if not live:
        return True, "no live marker (first Actions deployment)"
    lm, ls = int(live.get("main_commit_time") or 0), int(live.get("state_commit_time") or 0)
    if lm > main_time or ls > state_time:
        return False, (f"live site is newer (main {live.get('main_sha', '')[:10]}@{lm}, "
                       f"state {live.get('state_commit', '')[:10]}@{ls}) than this build "
                       f"(main {main_sha[:10]}@{main_time}, state {state_commit[:10]}@{state_time})")
    if live.get("main_sha") == main_sha and live.get("state_commit") == state_commit and not force:
        return False, f"already deployed (main {main_sha[:10]}, state {state_commit[:10]})"
    return True, "newer than the live site"


def count_xml_feeds(names: list[str]) -> int:
    """Top-level feeds/*.xml files (names relative to feeds/)."""
    return sum(1 for n in names if "/" not in n and n.endswith(".xml"))


def entries_regressions(site: dict[str, int], ref: dict[str, int]) -> list[str]:
    """Human-readable problems: an entries.json missing or with fewer episodes."""
    out = []
    for name, n in sorted(ref.items()):
        if name not in site:
            out.append(f"{name}: missing from the build (main has {n})")
        elif site[name] < n:
            out.append(f"{name}: {site[name]} episodes < {n} on main")
    return out


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _git(repo: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True).stdout


def _output(**kv) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            for k, v in kv.items():
                fh.write(f"{k}={v}\n")


def _summary(text: str) -> None:
    print(text)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")


def fetch(url: str, timeout: int = 30) -> tuple[int, dict, bytes]:
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), b""


def bust(base: str, path: str) -> str:
    return f"{base.rstrip('/')}/{quote(path, safe='/-._~')}?cb={time.time_ns()}"


def live_marker(base: str) -> dict | None:
    code, _h, body = fetch(bust(base, "_deploy.json"))
    if code != 200:
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except ValueError:
        return None


def ref_tree_names(repo: Path, ref: str, prefix: str) -> dict[str, str]:
    """name (relative to prefix/) -> blob sha, one level only (no blob read)."""
    out: dict[str, str] = {}
    raw = _git(repo, "ls-tree", "-z", "--full-tree", f"{ref}:{prefix}")
    for rec in raw.split(b"\0"):
        if not rec:
            continue
        meta, name = rec.split(b"\t", 1)
        _mode, typ, sha = meta.split(b" ")
        if typ == b"blob":
            out[name.decode("utf-8", "surrogateescape")] = sha.decode()
    return out


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def cmd_freshness(args) -> int:
    live = live_marker(args.base)
    ok, reason = freshness_verdict(live, args.main_sha, args.main_time,
                                   args.state_commit, args.state_time, args.force)
    _summary(f"### Freshness\n- {'DEPLOY' if ok else 'SKIP'}: {reason}")
    _output(deploy=str(ok).lower(), reason=reason.replace("\n", " "))
    return 0


def cmd_drift(args) -> int:
    repo = Path(args.repo)
    if args.state == args.main:
        _output(drift="false")
        _summary("### State drift\n- R2 state commit IS main: no drift")
        return 0
    changed = _git(repo, "diff-tree", "-r", "--name-only", "--no-renames", args.state, args.main,
                   "--", *STATE_PATHS).decode("utf-8", "surrogateescape").split()
    if not changed:
        _output(drift="false")
        _summary(f"### State drift\n- main {args.main[:10]} and R2 state {args.state[:10]} "
                 "carry the same state files: no drift")
        return 0
    main_time = int(_git(repo, "show", "-s", "--format=%ct", args.main).decode().strip())
    lag_h = (time.time() - main_time) / 3600
    msg = (f"main {args.main[:10]} changed {len(changed)} state file(s) not yet mirrored to R2 "
           f"(R2 state = {args.state[:10]}), e.g. {changed[:5]}; main commit is {lag_h:.1f} h old")
    _output(drift="true")
    if lag_h > args.max_lag_hours:
        _summary(f"### State drift\n- FAIL: {msg} (> {args.max_lag_hours} h: the R2 mirror is stuck)")
        print("::error::" + msg)
        return 1
    _summary(f"### State drift\n- SKIP: {msg}; the next pipeline run mirrors it and redeploys")
    print("::warning::" + msg)
    return 0


def _changed_since_state(repo: Path, state: str, main: str) -> list[str]:
    if state == main:
        return []
    raw = _git(repo, "diff-tree", "-r", "-z", "--name-only", "--no-renames", state, main)
    return [p for p in raw.decode("utf-8", "surrogateescape").split("\0") if p]


def cmd_pin(args) -> int:
    """Generate with the INPUTS of the state commit.

    The committed artefacts of `main` were generated by the pipeline run that
    mirrored the state (commit S). Later commits on main (social_state.json
    bumps, human source edits) are served by legacy Pages as is, but did not
    regenerate anything: rebuilding with them would publish artefacts main
    does not have (e.g. the home spotlight follows social_state.json). So the
    source files that changed between S and main are pinned to their S
    version for the generation, then put back (`unpin`) before comparing and
    publishing: the site is then exactly main's tree.
    """
    repo = Path(args.repo)
    changed = _changed_since_state(repo, args.state, args.main)
    tags: dict[str, str] = {}
    if changed:
        raw = _git(repo, "ls-files", "-t", "-z", "--", *changed).decode("utf-8", "surrogateescape")
        for rec in raw.split("\0"):
            if rec:
                tags[rec[2:]] = rec[0]
    in_state = set()
    if changed:
        raw = _git(repo, "ls-tree", "-z", "--name-only", "--full-tree", args.state, "--", *changed)
        in_state = {p for p in raw.decode("utf-8", "surrogateescape").split("\0") if p}
    pinned = []
    for p in changed:
        if tags.get(p) == "S":
            continue  # outside the source checkout (generated output): not an input
        f = repo / p
        if p in in_state:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(_git(repo, "cat-file", "blob", f"{args.state}:{p}"))
        elif f.exists():
            f.unlink()
        pinned.append(p)
    Path(args.save).write_text(json.dumps(pinned, ensure_ascii=False, indent=1), encoding="utf-8")
    _summary(f"### Generation inputs pinned to the state commit {args.state[:10]}\n"
             f"- {len(changed)} file(s) changed on main since, {len(pinned)} pinned"
             + "".join(f"\n  - `{p}`" for p in pinned[:30]))
    return 0


def cmd_unpin(args) -> int:
    repo = Path(args.repo)
    pinned = json.loads(Path(args.save).read_text(encoding="utf-8"))
    for p in pinned:
        f = repo / p
        try:
            data = _git(repo, "cat-file", "blob", f"{args.main}:{p}")
        except subprocess.CalledProcessError:
            if f.exists():
                f.unlink()
            continue
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
    print(f"restored {len(pinned)} pinned file(s) to main {args.main[:10]}")
    return 0


def cmd_content(args) -> int:
    site, repo = Path(args.site), Path(args.repo)
    ref_feeds = ref_tree_names(repo, args.ref, "feeds")
    site_feeds = [p.name for p in (site / "feeds").iterdir() if p.is_file()]
    n_site, n_ref = count_xml_feeds(site_feeds), count_xml_feeds(list(ref_feeds))
    problems = []
    if n_site != n_ref:
        problems.append(f"{n_site} feeds/*.xml in the build vs {n_ref} on main")
    ref_entries = {n: s for n, s in ref_feeds.items() if n.endswith(".entries.json")}
    ref_counts = {n: len(json.loads(_git(repo, "cat-file", "blob", s).decode("utf-8-sig")))
                  for n, s in ref_entries.items()}
    site_counts = {}
    for n in site_feeds:
        if n.endswith(".entries.json"):
            site_counts[n] = len(json.loads((site / "feeds" / n).read_text(encoding="utf-8-sig")))
    problems += entries_regressions(site_counts, ref_counts)
    lines = [f"### Content guard vs main {args.ref[:10]}",
             f"- feeds/*.xml: build {n_site} / main {n_ref}",
             f"- entries.json: build {len(site_counts)} files, {sum(site_counts.values())} episodes / "
             f"main {len(ref_counts)} files, {sum(ref_counts.values())} episodes"]
    lines += [f"- PROBLEM: {p}" for p in problems]
    _summary("\n".join(lines))
    _output(xml_feeds=n_site, episodes=sum(site_counts.values()))
    if problems:
        print("::error::content guard failed: " + "; ".join(problems[:5]))
        return 1
    return 0


def cmd_marker(args) -> int:
    site = Path(args.site)
    files = sum(len(f) for _d, _s, f in os.walk(site) if ".git" not in Path(_d).parts)
    marker = {
        "run_id": os.environ.get("GITHUB_RUN_ID", ""),
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
        "event": os.environ.get("GITHUB_EVENT_NAME", ""),
        "main_sha": args.main_sha,
        "main_commit_time": args.main_time,
        "state_commit": args.state_commit,
        "state_commit_time": args.state_time,
        "legacy_orphans": args.legacy_orphans,
        "files": files + 1,
        "built_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    (site / "_deploy.json").write_text(json.dumps(marker, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(marker, indent=1))
    return 0


def cmd_verify(args) -> int:
    base = args.base.rstrip("/")
    t0 = time.time()
    while True:
        m = live_marker(base)
        if m and str(m.get("run_id")) == str(args.run_id):
            break
        if time.time() - t0 > args.timeout:
            print(f"::error::marker of run {args.run_id} not served after {args.timeout}s (live: {m})")
            return 1
        time.sleep(5)
    lines = [f"### Live verification of {base}",
             f"- marker of run {args.run_id} served {time.time() - t0:.0f}s after polling started",
             f"- main {m.get('main_sha', '')[:10]}, state {m.get('state_commit', '')[:10]}"]
    ref = m["main_sha"]
    repo = Path(args.repo)
    fails: list[str] = []
    checked = 0

    def check(path: str, ctype: str = "", sha_ref: bool = False, parse_json: bool = False) -> bytes:
        nonlocal checked
        code, headers, body = fetch(bust(base, path))
        checked += 1
        h = {k.lower(): v for k, v in headers.items()}
        if code != 200:
            fails.append(f"{path}: HTTP {code}")
            return b""
        if ctype and not h.get("content-type", "").startswith(ctype):
            fails.append(f"{path}: content-type {h.get('content-type')!r}, expected {ctype}")
        if parse_json:
            try:
                json.loads(body.decode("utf-8-sig"))
            except ValueError as e:
                fails.append(f"{path}: invalid JSON ({e})")
        if sha_ref:
            try:
                want = _git(repo, "cat-file", "blob", f"{ref}:{path}")
            except subprocess.CalledProcessError:
                fails.append(f"{path}: not in git {ref[:10]}")
                return body
            if hashlib.sha256(body).hexdigest() != hashlib.sha256(want).hexdigest():
                fails.append(f"{path}: sha256 differs from git {ref[:10]}")
        return body

    feeds = sorted(n for n in ref_tree_names(repo, ref, "feeds") if n.endswith(".xml") and "/" not in n)
    for n in feeds:
        check(f"feeds/{n}", "application/xml", sha_ref=True)
    lines.append(f"- {len(feeds)} feeds/*.xml: HTTP 200 + application/xml + sha256 == git")
    for p in ("channels.json", "home.json", "mobile/manifest.json", "search-fts/manifest.json"):
        check(p, "application/json", parse_json=True)
    chans = json.loads(check("channels.json").decode("utf-8-sig") or "[]")
    if chans:
        check(f"artwork/thumb/{chans[0]['slug']}.webp", "image/webp")
    check("sitemap.xml", "application/xml")
    check("index.html", "text/html")
    check("", "text/html")
    latest = json.loads(check("latest.json", "application/json").decode("utf-8-sig") or "{}")
    eps = [e["url"] for e in latest.get("episodes", []) if e.get("url")][:2]
    for u in eps:
        check(u.lstrip("/"), "text/html")
    legacy = [line.strip() for line in Path(args.legacy_list).read_text(encoding="utf-8").splitlines()
              if line.strip() and not line.startswith("#")]
    for p in (legacy[0], legacy[len(legacy) // 2], legacy[-1]) if legacy else ():
        check(p, "text/html", sha_ref=True)
    for n in ref_tree_names(repo, ref, ".well-known"):
        check(f".well-known/{n}", sha_ref=True)
    code, headers, _ = fetch("http://" + base.split("://", 1)[1] + "/index.html")
    lines.append(f"- http:// -> final HTTP {code} (urllib follows the redirect to https)")
    lines.append(f"- {checked} URL(s) probed, {len(fails)} failure(s)")
    lines += [f"- FAIL: {f}" for f in fails]
    _summary("\n".join(lines))
    return 1 if fails else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    base = "https://thetorahpodcast.net"

    s = sub.add_parser("freshness")
    s.add_argument("--base", default=base)
    s.add_argument("--main-sha", required=True)
    s.add_argument("--main-time", type=int, required=True)
    s.add_argument("--state-commit", required=True)
    s.add_argument("--state-time", type=int, required=True)
    s.add_argument("--force", action="store_true", help="redeploy even if already live")
    s.set_defaults(func=cmd_freshness)

    s = sub.add_parser("drift")
    s.add_argument("--repo", required=True)
    s.add_argument("--state", required=True)
    s.add_argument("--main", required=True)
    s.add_argument("--max-lag-hours", type=float, default=3.0)
    s.set_defaults(func=cmd_drift)

    for name, fn in (("pin", cmd_pin), ("unpin", cmd_unpin)):
        s = sub.add_parser(name)
        s.add_argument("--repo", required=True)
        s.add_argument("--state", default="")
        s.add_argument("--main", required=True)
        s.add_argument("--save", required=True, help="JSON list of the pinned paths")
        s.set_defaults(func=fn)

    s = sub.add_parser("content")
    s.add_argument("--site", required=True)
    s.add_argument("--repo", required=True)
    s.add_argument("--ref", required=True)
    s.set_defaults(func=cmd_content)

    s = sub.add_parser("marker")
    s.add_argument("--site", required=True)
    s.add_argument("--main-sha", required=True)
    s.add_argument("--main-time", type=int, required=True)
    s.add_argument("--state-commit", required=True)
    s.add_argument("--state-time", type=int, required=True)
    s.add_argument("--legacy-orphans", type=int, default=0)
    s.set_defaults(func=cmd_marker)

    s = sub.add_parser("verify")
    s.add_argument("--base", default=base)
    s.add_argument("--repo", required=True, help="git repo holding the live main commit (blobless OK)")
    s.add_argument("--run-id", required=True)
    s.add_argument("--timeout", type=int, default=900)
    s.add_argument("--legacy-list", default=str(Path(__file__).resolve().parent / "legacy_orphans.txt"))
    s.set_defaults(func=cmd_verify)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
