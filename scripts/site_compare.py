"""Compare a rebuilt site directory with a git tree, file by file.

Used by .github/workflows/build-site.yml (phase 1 of moving generated
artefacts out of git). Blobless-friendly: the reference side is read with
`git ls-tree -r -z` (tree objects only, no blob download), the site side with
`git hash-object --stdin-paths`. Blob contents are fetched ONLY for the files
that differ, to decide whether the difference is a tolerated one.

Tolerated differences (explicit, nothing else is tolerated):
  * JSON `generated_at` timestamps (home.json, latest.json, mobile/*.json):
    the files must be byte-identical once every `"generated_at": "..."` value
    is masked.
  * sitemap.xml `<lastmod>`: the generator stamps the channel/static URLs
    with the build day. Same number of lines, and every differing line must
    be a `<lastmod>` line whose rebuilt value is one of the build dates.

Legacy orphan pages (--keep-legacy-orphans): HTML pages present in the tree
that the generator no longer produces (old episode filenames, renamed titles,
disabled channels). They are still served today (HTTP 200) but are in no
sitemap. They are NOT hidden: each one must be absent from the REBUILT
sitemap.xml (proof it is not a page the build should have produced), is
copied verbatim from git into the site, and is reported in its own
`legacy_kept` category. Any other missing file stays "missing".

Categories: identical / tolerated / legacy_kept / different / missing (in the
tree, not rebuilt) / extra (rebuilt, not in the tree).
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote

SKIP_DIRS = {".git", "__pycache__"}
SKIP_SUFFIXES = (".pyc", ".pyo")
MAX_ANALYSED = 6000  # blobs fetched for tolerance analysis
DIFF_SAMPLES = 15   # unified-diff excerpts kept in the report

_GEN_AT = re.compile(r'("generated_at"\s*:\s*)"[^"]*"')
_LASTMOD = re.compile(r"^\s*<lastmod>(\d{4}-\d{2}-\d{2})</lastmod>\s*$")


# --------------------------------------------------------------------------
# Pure helpers (unit-tested)
# --------------------------------------------------------------------------

def slug_scope(path: str, slug: str) -> bool:
    """Files that belong to one channel/speaker (the only_slug test)."""
    low = slug.lower()
    top = path.split("/", 1)[0]
    if top in (slug, low) and "/" in path:
        return True  # episode-page directory
    if path in (f"{slug}.html", f"{low}.html"):
        return True
    for d in ("feeds/", "artwork/", "artwork/thumb/"):
        if path.startswith(d):
            name = path[len(d):]
            if "/" in name:
                continue
            stem = name.split(".", 1)[0]
            if stem == slug:
                return True
    return False


_LOC = re.compile(r"<loc>\s*https?://[^/<]+/([^<]*?)\s*</loc>")


def sitemap_paths(text: str) -> set[str]:
    """Site-relative paths listed in a sitemap (raw and percent-decoded)."""
    out: set[str] = set()
    for m in _LOC.finditer(text):
        out.add(m.group(1))
        out.add(unquote(m.group(1)))
    return out


def is_legacy_orphan(path: str, sitemap: set[str]) -> bool:
    """A missing file that may be kept verbatim from git: an HTML page that the
    rebuilt sitemap does not list. Never data files (feeds, JSON, ...)."""
    return path.endswith(".html") and path not in sitemap


def mask_generated_at(text: str) -> str:
    return _GEN_AT.sub(r'\1"*"', text)


def tolerate(path: str, site: bytes, ref: bytes, build_dates: set[str]) -> str | None:
    """Return the tolerance reason, or None if the difference is real."""
    try:
        s, r = site.decode("utf-8"), ref.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if path.endswith(".json") and '"generated_at"' in s:
        if mask_generated_at(s) == mask_generated_at(r):
            return "generated_at"
        return None
    if path == "sitemap.xml":
        sl, rl = s.split("\n"), r.split("\n")
        if len(sl) != len(rl):
            return None
        for a, b in zip(sl, rl):
            if a == b:
                continue
            ma, mb = _LASTMOD.match(a), _LASTMOD.match(b)
            if not (ma and mb and ma.group(1) in build_dates):
                return None
        return "lastmod"
    return None


def classify(site: dict[str, str], ref: dict[str, str]) -> dict[str, list[str]]:
    """site/ref: path -> blob sha. Returns identical/different/missing/extra."""
    out = {"identical": [], "different": [], "missing": [], "extra": []}
    for p, sha in site.items():
        if p not in ref:
            out["extra"].append(p)
        elif ref[p] == sha:
            out["identical"].append(p)
        else:
            out["different"].append(p)
    out["missing"] = [p for p in ref if p not in site]
    for v in out.values():
        v.sort()
    return out


def group_counts(paths: list[str]) -> dict[str, int]:
    c = Counter()
    for p in paths:
        c[p.split("/", 1)[0] + ("/" if "/" in p else "")] += 1
    return dict(c.most_common())


# --------------------------------------------------------------------------
# git plumbing
# --------------------------------------------------------------------------

def git(repo: Path, *args: str, input: bytes | None = None) -> bytes:
    return subprocess.run(["git", "-C", str(repo), *args], input=input,
                          check=True, capture_output=True).stdout


def ref_tree(repo: Path, ref: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for rec in git(repo, "ls-tree", "-r", "-z", "--full-tree", ref).split(b"\0"):
        if not rec:
            continue
        meta, path = rec.split(b"\t", 1)
        mode, typ, sha = meta.split(b" ")
        if typ != b"blob":
            continue  # submodules
        out[path.decode("utf-8", "surrogateescape")] = sha.decode()
    return out


def site_files(site: Path) -> list[str]:
    files: list[str] = []
    for dirpath, dirnames, filenames in os.walk(site):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for n in filenames:
            if n.endswith(SKIP_SUFFIXES):
                continue
            files.append(Path(dirpath, n).relative_to(site).as_posix())
    return sorted(files)


def hash_files(repo: Path, site: Path, files: list[str]) -> dict[str, str]:
    # --no-filters: hash the RAW bytes, i.e. exactly what Pages would serve,
    # against the committed blob (also what Pages serves today). With filters,
    # `* text=auto eol=lf` turned CRLF into LF in the hash, while the pipeline's
    # own `git add` keeps CRLF for files whose index copy already has CRLF
    # (git's "has CRLF in index" safety rule): 795 pages with CRLF inside
    # episode descriptions showed up as different although byte-identical
    # (full run 36385404670).
    data = "\n".join(files).encode("utf-8", "surrogateescape") + b"\n"
    shas = git(repo, "hash-object", "--no-filters", "--stdin-paths", input=data).decode().split()
    if len(shas) != len(files):
        raise RuntimeError(f"hash-object returned {len(shas)} hashes for {len(files)} files")
    return dict(zip(files, shas))


def prefetch_blobs(repo: Path, shas: list[str]) -> None:
    """Batch-download missing blobs of a partial clone (same command git's
    promisor code runs, but with many objects per round-trip)."""
    for i in range(0, len(shas), 1000):
        chunk = "\n".join(shas[i:i + 1000]).encode() + b"\n"
        r = subprocess.run(["git", "-C", str(repo), "-c", "fetch.negotiationAlgorithm=noop",
                            "fetch", "origin", "--no-tags", "--no-write-fetch-head",
                            "--recurse-submodules=no", "--filter=blob:none", "--stdin"],
                           input=chunk, capture_output=True)
        if r.returncode:
            # Not fatal: cat-file falls back to one lazy fetch per blob (slower).
            print(f"prefetch of {len(shas[i:i + 1000])} blobs failed: "
                  f"{r.stderr.decode(errors='replace')[:300]}")


def read_blob(repo: Path, sha: str) -> bytes:
    return git(repo, "cat-file", "blob", sha)


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--site", required=True, help="rebuilt site directory (a git work tree)")
    ap.add_argument("--ref", required=True, help="commit to compare against")
    ap.add_argument("--repo", default="", help="git repo holding --ref (default: --site)")
    ap.add_argument("--only-slug", default="")
    ap.add_argument("--build-date", action="append", default=[],
                    help="YYYY-MM-DD the generator may have stamped (repeatable)")
    ap.add_argument("--report-dir", required=True)
    ap.add_argument("--extra-info", default="", help="JSON object merged into the report header")
    ap.add_argument("--keep-legacy-orphans", action="store_true",
                    help="copy missing HTML pages absent from the rebuilt sitemap from git into the site")
    ap.add_argument("--strict", action="store_true", help="exit 1 on any non-tolerated difference")
    args = ap.parse_args(argv)

    site = Path(args.site).resolve()
    repo = Path(args.repo).resolve() if args.repo else site
    if repo != site:
        # hash_files() hands work-tree-relative paths to git: the site must BE
        # the repo's work tree for .gitattributes to apply like `git add`.
        ap.error("--repo must be the work tree of --site")
    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).date()
    build_dates = set(args.build_date) | {today.isoformat(), (today - timedelta(days=1)).isoformat()}

    ref = ref_tree(repo, args.ref)
    files = site_files(site)
    if args.only_slug:
        ref = {p: s for p, s in ref.items() if slug_scope(p, args.only_slug)}
        files = [p for p in files if slug_scope(p, args.only_slug)]
    site_hashes = hash_files(repo, site, files)
    cats = classify(site_hashes, ref)

    legacy: list[str] = []
    if args.keep_legacy_orphans and cats["missing"]:
        smap = site / "sitemap.xml"
        listed = sitemap_paths(smap.read_text(encoding="utf-8")) if smap.is_file() else None
        if listed is None:
            print("::warning::no rebuilt sitemap.xml - legacy orphans NOT kept")
        else:
            legacy = [p for p in cats["missing"] if is_legacy_orphan(p, listed)]
            prefetch_blobs(repo, [ref[p] for p in legacy])
            for p in legacy:
                out = site / p
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(read_blob(repo, ref[p]))
            kept = set(legacy)
            cats["missing"] = [p for p in cats["missing"] if p not in kept]

    tolerated: dict[str, str] = {}
    unanalysed: list[str] = []
    diff = cats["different"]
    to_check = diff[:MAX_ANALYSED]
    unanalysed = diff[MAX_ANALYSED:]
    prefetch_blobs(repo, [ref[p] for p in to_check])
    real: list[str] = []
    for p in to_check:
        try:
            reason = tolerate(p, (site / p).read_bytes(), read_blob(repo, ref[p]), build_dates)
        except subprocess.CalledProcessError:
            reason = None
        if reason:
            tolerated[p] = reason
        else:
            real.append(p)
    real += unanalysed

    # A few unified-diff excerpts (one per top-level group first) so a real
    # difference can be diagnosed from the report alone.
    samples: dict[str, str] = {}
    seen_groups: set[str] = set()
    ordered = [p for p in real if not (p.split("/", 1)[0] in seen_groups or seen_groups.add(p.split("/", 1)[0]))]
    ordered += [p for p in real if p not in ordered]
    for p in [q for q in ordered if q not in unanalysed][:DIFF_SAMPLES]:
        try:
            a = read_blob(repo, ref[p]).decode("utf-8", "replace").splitlines()
            b = (site / p).read_bytes().decode("utf-8", "replace").splitlines()
        except (OSError, subprocess.CalledProcessError):
            continue
        lines = list(difflib.unified_diff(a, b, "git/" + p, "rebuilt/" + p, n=0, lineterm=""))
        samples[p] = "\n".join(line[:400] for line in lines[:40])

    header = {
        "ref": args.ref,
        "only_slug": args.only_slug or None,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "build_dates_tolerated": sorted(build_dates),
        "counts": {
            "ref_files": len(ref),
            "site_files": len(files),
            "identical": len(cats["identical"]),
            "tolerated": len(tolerated),
            "legacy_kept": len(legacy),
            "different": len(real),
            "missing": len(cats["missing"]),
            "extra": len(cats["extra"]),
            "different_not_analysed": len(unanalysed),
        },
        "tolerated_by_reason": dict(Counter(tolerated.values())),
    }
    if args.extra_info:
        header.update(json.loads(args.extra_info))
    non_tolerated = len(real) + len(cats["missing"]) + len(cats["extra"])
    header["non_tolerated_total"] = non_tolerated

    report = dict(header)
    report.update({
        "tolerated": tolerated,
        "legacy_kept": legacy,
        "different": real,
        "missing": cats["missing"],
        "extra": cats["extra"],
        "diff_samples": samples,
        "groups": {k: group_counts(v) for k, v in
                   (("legacy_kept", legacy), ("different", real), ("missing", cats["missing"]),
                    ("extra", cats["extra"]))},
    })
    (report_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    md = [f"## Site rebuild vs `{args.ref}`" + (f" (only `{args.only_slug}`)" if args.only_slug else ""), ""]
    md.append("| category | files |\n|---|---|")
    for k, v in header["counts"].items():
        md.append(f"| {k} | {v} |")
    md.append(f"\n**Non-tolerated total: {non_tolerated}**  ")
    md.append(f"Tolerated by reason: {header['tolerated_by_reason'] or 'none'}\n")
    for name, lst in (("legacy_kept", legacy), ("different", real), ("missing", cats["missing"]),
                      ("extra", cats["extra"])):
        if not lst:
            continue
        md.append(f"### {name} ({len(lst)}) — by top-level entry")
        for g, n in list(group_counts(lst).items())[:40]:
            md.append(f"- `{g}` {n}")
        md.append("\nFirst entries:")
        for p in lst[:60]:
            md.append(f"- `{p}`")
        md.append("")
    if samples:
        md.append(f"### diff samples ({len(samples)})")
        for p, d in samples.items():
            md.append(f"\n`{p}`\n```diff\n{d}\n```")
    text = "\n".join(md) + "\n"
    (report_dir / "report.md").write_text(text, encoding="utf-8")
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(text)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
            fh.write(f"non_tolerated={non_tolerated}\n")
    return 1 if (args.strict and non_tolerated) else 0


if __name__ == "__main__":
    sys.exit(main())
