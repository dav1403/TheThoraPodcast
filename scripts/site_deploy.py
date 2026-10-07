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

Phase 4 (state in R2, no reference in git): freshness2, guard,
capture-vanished, record, promote, indexnow, verify2 - the build is compared
with the last deployment verified live (R2 `_deploy/`), not with `main`.

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
        "state_generation": args.state_generation,
        "files": files + 1,
        "built_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    if args.feeds_md5:
        marker["feeds_md5"] = json.loads(Path(args.feeds_md5).read_text(encoding="utf-8"))
    (site / "_deploy.json").write_text(json.dumps(marker, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in marker.items() if k != "feeds_md5"}, indent=1))
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


# --------------------------------------------------------------------------
# Phase 4: no reference in git any more — compare with the previous deploy
# --------------------------------------------------------------------------
#
# R2 layout (prefix `_deploy/`, `_deploy-staging/` off main):
#   _deploy/runs/<run_id>/summary.json   written by the build of every run
#   _deploy/runs/<run_id>/files.txt.gz   every path of that build
#   _deploy/last.json, _deploy/files.txt.gz
#                                        the last deployment VERIFIED live
#                                        (promoted by the verify job)

DEPLOY_PREFIX = os.environ.get("TTP_DEPLOY_PREFIX", "_deploy/")
EPISODE_PAGE_LIMIT = 100          # IndexNow: per submission
VANISH_LIMIT = 50                 # pages that may disappear in one deploy
MIN_FILES_RATIO = 0.98


def freshness_verdict_v2(live: dict | None, src_sha: str, src_time: int, generation: int,
                         force: bool) -> tuple[bool, str]:
    """(deploy?, reason). Ordering by state GENERATION (several generations
    share one source commit in phase 4), never older source either."""
    if not live:
        return True, "no live marker"
    lg = int(live.get("state_generation") or 0)
    lt = int(live.get("main_commit_time") or 0)
    if lg > generation:
        return False, f"live site has a newer state (generation {lg} > {generation})"
    if lt > src_time:
        return False, (f"live site has a newer source ({live.get('main_sha', '')[:10]}@{lt} > "
                       f"{src_sha[:10]}@{src_time})")
    if live.get("main_sha") == src_sha and lg == generation and not force:
        return False, f"already deployed (source {src_sha[:10]}, generation {generation})"
    return True, "newer than the live site"


def episode_pages(paths, slugs) -> list[str]:
    """<slug>/<file>.html pages of the channels and guest speakers."""
    dirs = set(slugs)
    return sorted(p for p in paths if p.endswith(".html") and p.count("/") == 1
                  and p.split("/", 1)[0] in dirs)


def guard_problems(prev: dict | None, cur: dict, allow_shrink: bool = False) -> list[str]:
    """Regression checks of a build against the last verified deployment."""
    out: list[str] = []
    if not prev:
        return out
    if not allow_shrink and cur["xml_feeds"] < prev.get("xml_feeds", 0):
        out.append(f"{cur['xml_feeds']} feeds/*.xml < {prev['xml_feeds']} in the last deployment")
    for name, n in sorted((prev.get("entries") or {}).items()):
        if name not in cur["entries"]:
            if not allow_shrink:
                out.append(f"{name}: missing (last deployment had {n} episodes)")
        elif cur["entries"][name] < n:
            out.append(f"{name}: {cur['entries'][name]} episodes < {n} in the last deployment")
    pf = int(prev.get("files") or 0)
    if not allow_shrink and pf and cur["files"] < pf * MIN_FILES_RATIO:
        out.append(f"{cur['files']} files < {MIN_FILES_RATIO:.0%} of the last deployment ({pf})")
    return out


def _site_paths(site: Path) -> list[str]:
    out = []
    for d, dirs, files in os.walk(site):
        dirs[:] = [x for x in dirs if x != ".git"]
        for f in files:
            out.append(Path(d, f).relative_to(site).as_posix())
    return sorted(out)


def build_summary(site: Path, paths: list[str]) -> dict:
    feeds = site / "feeds"
    entries = {}
    for p in paths:
        if p.startswith("feeds/") and p.endswith(".entries.json") and p.count("/") == 1:
            try:
                entries[p.split("/", 1)[1]] = len(json.loads((site / p).read_text(encoding="utf-8-sig")))
            except ValueError:
                entries[p.split("/", 1)[1]] = -1
    slugs = []
    for f in ("channels.json", "speakers.json"):
        if (site / f).is_file():
            slugs += [r.get("slug") for r in json.loads((site / f).read_text(encoding="utf-8-sig")) if r.get("slug")]
    xml_md5 = {}
    if feeds.is_dir():
        for p in sorted(feeds.iterdir()):
            if p.is_file() and p.suffix == ".xml":
                xml_md5[p.name] = hashlib.md5(p.read_bytes()).hexdigest()
    return {"files": len(paths), "xml_feeds": len(xml_md5), "entries": entries,
            "episodes": sum(v for v in entries.values() if v > 0), "slugs": slugs, "feeds_md5": xml_md5}


def _r2():
    import site_state
    return site_state.r2_client(8)


def _r2_get(s3, bucket, key) -> bytes | None:
    try:
        return s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    except Exception as exc:
        if "NoSuchKey" in str(exc) or "404" in str(exc):
            return None
        raise


def _files_txt(data: bytes | None) -> list[str]:
    import gzip
    if not data:
        return []
    return [line for line in gzip.decompress(data).decode("utf-8").splitlines() if line]


def cmd_freshness2(args) -> int:
    live = live_marker(args.base)
    ok, reason = freshness_verdict_v2(live, args.src_sha, args.src_time, args.generation, args.force)
    _summary(f"### Freshness (phase 4)\n- {'DEPLOY' if ok else 'SKIP'}: {reason}")
    _output(deploy=str(ok).lower(), reason=reason.replace("\n", " "))
    return 0


def cmd_guard(args) -> int:
    """Build vs the last verified deployment + restored feeds vs the state manifest."""
    import site_state
    site = Path(args.site)
    paths = _site_paths(site)
    cur = build_summary(site, paths)
    s3, bucket = _r2()
    prev_raw = _r2_get(s3, bucket, DEPLOY_PREFIX + "last.json")
    prev = json.loads(prev_raw) if prev_raw else None
    problems = guard_problems(prev, cur, args.allow_shrink)

    # Feeds of the build = feeds of the state generation that was restored.
    rec = json.loads(Path(args.record).read_text(encoding="utf-8"))
    man, _etag = site_state.get_manifest_etag(s3, bucket)
    if man and site_state.generation_of(man) == rec.get("generation"):
        bad = [n for n, md5 in cur["feeds_md5"].items()
               if (man["files"].get(f"feeds/{n}") or {}).get("md5") not in (None, md5)]
        problems += [f"feeds/{n}: differs from the state manifest" for n in bad]
    else:
        print("::notice::state moved since the restore: feed MD5 check against the manifest skipped "
              "(the restore itself was MD5-verified)")

    # Pages that vanished since the last deployment (renamed title...).
    vanished: list[str] = []
    if prev:
        prev_paths = set(_files_txt(_r2_get(s3, bucket, DEPLOY_PREFIX + "files.txt.gz")))
        vanished = episode_pages(prev_paths - set(paths), prev.get("slugs") or cur["slugs"])
    lines = [f"### Guard vs the last deployment {('run ' + str(prev.get('run_id'))) if prev else '(none yet)'}",
             f"- build: {cur['files']} files, {cur['xml_feeds']} feeds/*.xml, "
             f"{len(cur['entries'])} entries.json, {cur['episodes']} episodes",
             (f"- last: {prev.get('files')} files, {prev.get('xml_feeds')} feeds/*.xml, "
              f"{len(prev.get('entries') or {})} entries.json, {prev.get('episodes')} episodes") if prev else "- last: none",
             f"- episode pages gone since the last deployment: {len(vanished)}"]
    if len(vanished) > VANISH_LIMIT and not args.allow_shrink:
        problems.append(f"{len(vanished)} episode pages would vanish (> {VANISH_LIMIT}): {vanished[:5]}")
    elif vanished:
        Path(args.vanished_out).write_text("\n".join(vanished) + "\n", encoding="utf-8")
        lines.append("  - kept online as legacy orphans (captured from the live site): "
                     + ", ".join(vanished[:10]))
    lines += [f"- PROBLEM: {p}" for p in problems]
    _summary("\n".join(lines))
    _output(vanished=len(vanished), files=cur["files"], episodes=cur["episodes"])
    if problems:
        print("::error::deploy guard failed: " + "; ".join(problems[:5]))
        return 1
    return 0


def cmd_capture_vanished(args) -> int:
    """Keep pages that disappeared online: fetch them from the live site
    (still serving the previous deployment), store them in `_static-legacy/`
    as extra orphans and put them in this build."""
    import base64

    import site_legacy
    lst = Path(args.list)
    paths = [p for p in lst.read_text(encoding="utf-8").splitlines() if p] if lst.is_file() else []
    if not paths:
        print("nothing to capture")
        return 0
    s3, bucket = _r2()
    manifest = site_legacy.get_manifest(s3, bucket)
    files = manifest.setdefault("files", {})
    got = 0
    for p in paths:
        code, _h, body = fetch(bust(args.base, p))
        if code != 200 or not body:
            print(f"::warning::{p}: HTTP {code} on the live site, not captured")
            continue
        md5 = hashlib.md5(body).hexdigest()
        if not args.dry_run:
            s3.put_object(Bucket=bucket, Key=site_legacy.PREFIX + p, Body=body,
                          ContentType="text/html; charset=utf-8",
                          ContentMD5=base64.b64encode(bytes.fromhex(md5)).decode())
            files[p] = {"md5": md5, "size": len(body), "listed": False,
                        "captured_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat()}
        out = Path(args.site) / p
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(body)
        got += 1
    if got and not args.dry_run:
        site_legacy.put_manifest(s3, bucket, manifest)
    print(f"captured {got}/{len(paths)} vanished page(s){' (dry run: not stored)' if args.dry_run else ''}")
    return 0


def cmd_record(args) -> int:
    """Store this build's summary + file list under runs/<run_id>/."""
    import gzip
    site = Path(args.site)
    paths = _site_paths(site)
    summ = build_summary(site, paths)
    summ.update({"run_id": args.run_id, "state_generation": args.generation, "main_sha": args.src_sha,
                 "built_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat()})
    Path(args.marker_md5).write_text(json.dumps(summ["feeds_md5"]), encoding="utf-8")
    s3, bucket = _r2()
    pre = f"{DEPLOY_PREFIX}runs/{args.run_id}/"
    s3.put_object(Bucket=bucket, Key=pre + "summary.json", ContentType="application/json",
                  Body=json.dumps(summ, ensure_ascii=False).encode("utf-8"))
    s3.put_object(Bucket=bucket, Key=pre + "files.txt.gz", ContentType="application/gzip",
                  Body=gzip.compress(("\n".join(paths) + "\n").encode("utf-8")))
    print(f"recorded {len(paths)} paths under {pre}")
    return 0


def cmd_promote(args) -> int:
    """After a verified deployment: runs/<id>/ becomes last.json/files.txt.gz;
    list the episode pages new since the previous one (IndexNow)."""
    s3, bucket = _r2()
    pre = f"{DEPLOY_PREFIX}runs/{args.run_id}/"
    summ_raw = _r2_get(s3, bucket, pre + "summary.json")
    files_raw = _r2_get(s3, bucket, pre + "files.txt.gz")
    if not summ_raw or not files_raw:
        print(f"::error::nothing recorded under {pre}")
        return 1
    summ = json.loads(summ_raw)
    prev_files = _files_txt(_r2_get(s3, bucket, DEPLOY_PREFIX + "files.txt.gz"))
    new_pages: list[str] = []
    if prev_files:
        new_pages = episode_pages(set(_files_txt(files_raw)) - set(prev_files), summ.get("slugs") or [])
    else:
        print("no previous deployment recorded: no IndexNow submission this time (first run)")
    Path(args.new_urls).write_text("\n".join(new_pages) + ("\n" if new_pages else ""), encoding="utf-8")
    s3.put_object(Bucket=bucket, Key=DEPLOY_PREFIX + "files.txt.gz", Body=files_raw,
                  ContentType="application/gzip")
    s3.put_object(Bucket=bucket, Key=DEPLOY_PREFIX + "last.json", ContentType="application/json",
                  Body=json.dumps({k: v for k, v in summ.items() if k != "feeds_md5"}, ensure_ascii=False).encode())
    # keep the last 20 runs
    runs = sorted({o["Key"][len(DEPLOY_PREFIX + "runs/"):].split("/", 1)[0]
                   for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=DEPLOY_PREFIX + "runs/")
                   for o in page.get("Contents", [])}, key=lambda r: int(r) if r.isdigit() else 0)
    for r in runs[:-20]:
        s3.delete_objects(Bucket=bucket, Delete={"Objects": [
            {"Key": f"{DEPLOY_PREFIX}runs/{r}/summary.json"}, {"Key": f"{DEPLOY_PREFIX}runs/{r}/files.txt.gz"}],
            "Quiet": True})
    _summary(f"### Promoted run {args.run_id} as the last verified deployment\n"
             f"- {summ.get('files')} files, generation {summ.get('state_generation')}\n"
             f"- new episode pages since the previous deployment: {len(new_pages)}")
    _output(new_pages=len(new_pages))
    return 0


INDEXNOW_KEY = "b9e2d8f1c3a74065b8e2d8f1c3a74065"  # public by design (served at /<key>.txt)


def cmd_indexnow(args) -> int:
    pages = [p for p in Path(args.list).read_text(encoding="utf-8").splitlines() if p][:EPISODE_PAGE_LIMIT]
    if not pages:
        print("IndexNow: no new episode pages to submit")
        return 0
    host = args.base.split("://", 1)[1].rstrip("/")
    urls = [f"{args.base.rstrip('/')}/{quote(p, safe='/-._~')}" for p in pages]
    if args.dry_run:
        print(f"IndexNow (dry run): would submit {len(urls)} URL(s), e.g. {urls[:3]}")
        return 0
    body = json.dumps({"host": host, "key": INDEXNOW_KEY,
                       "keyLocation": f"{args.base.rstrip('/')}/{INDEXNOW_KEY}.txt", "urlList": urls}).encode()
    req = urllib.request.Request("https://api.indexnow.org/indexnow", data=body, method="POST",
                                 headers={**UA, "Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    except Exception as e:  # best effort, never fails the deployment
        print(f"::warning::IndexNow: {type(e).__name__}")
        return 0
    print(f"IndexNow: submitted {len(urls)} new episode URL(s), HTTP {code}")
    return 0


def cmd_verify2(args) -> int:
    """Live checks without git: feeds vs the MD5 recorded in the marker,
    legacy orphans vs `_static-legacy/_manifest.json`, the rest like verify."""
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
    fails: list[str] = []
    checked = 0

    def check(path: str, ctype: str = "", md5: str = "", parse_json: bool = False) -> bytes:
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
        if md5 and hashlib.md5(body).hexdigest() != md5:
            fails.append(f"{path}: MD5 differs from the build")
        return body

    feeds_md5 = m.get("feeds_md5") or {}
    if not feeds_md5:
        fails.append("marker carries no feeds_md5")
    for n, md5 in sorted(feeds_md5.items()):
        check(f"feeds/{n}", "application/xml", md5=md5)
    for p in ("channels.json", "home.json", "latest.json", "search-index.json",
              "mobile/manifest.json", "search-fts/manifest.json"):
        check(p, "application/json", parse_json=True)
    chans = json.loads(check("channels.json").decode("utf-8-sig") or "[]")
    if chans:
        check(f"artwork/thumb/{chans[0]['slug']}.webp", "image/webp")
    check("sitemap.xml", "application/xml")
    check("index.html", "text/html")
    check("", "text/html")
    latest = json.loads(check("latest.json").decode("utf-8-sig") or "{}")
    for u in [e["url"] for e in latest.get("episodes", []) if e.get("url")][:2]:
        check(u.lstrip("/"), "text/html")
    legacy = [line.strip() for line in Path(args.legacy_list).read_text(encoding="utf-8").splitlines()
              if line.strip() and not line.startswith("#")]
    lman = {}
    if args.legacy_manifest and Path(args.legacy_manifest).is_file():
        lman = json.loads(Path(args.legacy_manifest).read_text(encoding="utf-8")).get("files", {})
    for p in (legacy[0], legacy[len(legacy) // 2], legacy[-1]) if legacy else ():
        check(p, "text/html", md5=(lman.get(p) or {}).get("md5", ""))
    repo = Path(args.repo)
    for n in ref_tree_names(repo, "HEAD", ".well-known"):
        want = _git(repo, "cat-file", "blob", f"HEAD:.well-known/{n}")
        check(f".well-known/{n}", md5=hashlib.md5(want).hexdigest())
    _summary("\n".join([f"### Live verification of {base} (phase 4)",
                        f"- marker of run {args.run_id} served {time.time() - t0:.0f}s after polling started",
                        f"- generation {m.get('state_generation')}, source {m.get('main_sha', '')[:10]}",
                        f"- {len(feeds_md5)} feeds/*.xml: HTTP 200 + application/xml + MD5 == build",
                        f"- {checked} URL(s) probed, {len(fails)} failure(s)"] + [f"- FAIL: {f}" for f in fails]))
    return 1 if fails else 0


def _add_phase4_parsers(sub, base: str) -> None:
    s = sub.add_parser("freshness2")
    s.add_argument("--base", default=base)
    s.add_argument("--src-sha", required=True)
    s.add_argument("--src-time", type=int, required=True)
    s.add_argument("--generation", type=int, required=True)
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_freshness2)

    s = sub.add_parser("guard")
    s.add_argument("--site", required=True)
    s.add_argument("--record", required=True, help="pull --record of the restored state")
    s.add_argument("--vanished-out", required=True)
    s.add_argument("--allow-shrink", action="store_true")
    s.set_defaults(func=cmd_guard)

    s = sub.add_parser("capture-vanished")
    s.add_argument("--site", required=True)
    s.add_argument("--list", required=True)
    s.add_argument("--base", default=base)
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(func=cmd_capture_vanished)

    s = sub.add_parser("record")
    s.add_argument("--site", required=True)
    s.add_argument("--run-id", required=True)
    s.add_argument("--generation", type=int, required=True)
    s.add_argument("--src-sha", required=True)
    s.add_argument("--marker-md5", required=True, help="write the feeds MD5 map here (for the marker)")
    s.set_defaults(func=cmd_record)

    s = sub.add_parser("promote")
    s.add_argument("--run-id", required=True)
    s.add_argument("--new-urls", required=True)
    s.set_defaults(func=cmd_promote)

    s = sub.add_parser("indexnow")
    s.add_argument("--list", required=True)
    s.add_argument("--base", default=base)
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(func=cmd_indexnow)

    s = sub.add_parser("verify2")
    s.add_argument("--base", default=base)
    s.add_argument("--repo", required=True, help="checkout holding .well-known/ (source)")
    s.add_argument("--run-id", required=True)
    s.add_argument("--timeout", type=int, default=900)
    s.add_argument("--legacy-manifest", default="")
    s.add_argument("--legacy-list", default=str(Path(__file__).resolve().parent / "legacy_orphans.txt"))
    s.set_defaults(func=cmd_verify2)


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
    s.add_argument("--state-generation", type=int, default=0)
    s.add_argument("--feeds-md5", default="", help="JSON map feeds/*.xml -> md5 (phase 4 verify)")
    s.set_defaults(func=cmd_marker)

    _add_phase4_parsers(sub, base)

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
