"""Persistent pipeline state <-> Cloudflare R2 (`_state/` prefix).

Phase 1 of "stop committing generated artefacts": git stays the source of
truth, this module only MIRRORS the state the hourly pipeline re-reads from one
run to the next, so that a site can later be rebuilt without those files being
in git. Nothing here is read by production yet.

What counts as state (re-read by the next run, cannot be recomputed from the
source tree alone) is defined ONCE, in STATE_DIRS / STATE_FILES below:

  feeds/        *.entries.json (THE course database — never lose it), *.xml,
                *.channel_info.json, ingest_state.json, rss_health.json,
                transcripts/*.txt (~34 000 files, ~600 MB)
  artwork/      masters + thumb/ (+ thumb/.state.json)
  search-fts/   inverted index + .build-state.json (append-only doc ids)
  processed.json, backfill_state.json

social_state.json deliberately stays in git (tiny, written by other workflows).

Layout in the bucket:
  _state/<path>                         one object per file, ETag == MD5
  _state/_manifest.json                 written LAST: commit, md5/size per file
  _state-snapshots/YYYY-MM-DD/state.tar.gz   daily, WITHOUT feeds/transcripts/
  _state-snapshots/YYYY-MM-DD/manifest.json

CLI (see --help):
  push      mirror the working tree's state to _state/ (+ --snapshot)
  pull      restore _state/ into a directory, MD5-verified against the manifest
  manifest  print the mirrored manifest header (commit, counts) / GITHUB_OUTPUT
  sparse-patterns  no-cone sparse-checkout patterns for a SOURCE-only checkout

Credentials come from the usual pipeline secrets: R2_ENDPOINT_URL,
R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME. No secret is ever
written into the state or the manifest.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import mimetypes
import os
import subprocess
import sys
import tarfile
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

# --------------------------------------------------------------------------
# Classification (single source of truth)
# --------------------------------------------------------------------------

STATE_DIRS = ("feeds", "artwork", "search-fts")
STATE_FILES = ("processed.json", "backfill_state.json")
# Excluded from the daily snapshot (bulk, and re-fetchable from YouTube).
SNAPSHOT_EXCLUDE_PREFIXES = ("feeds/transcripts/",)

# Root outputs of scripts/generate_channel_pages.py (see its main()).
GENERATED_ROOT_FILES = ("sitemap.xml", "home.json", "latest.json", "search-index.json")
# mobile/*.json are written by build_mobile_index.py, EXCEPT these sources.
MOBILE_SOURCE_JSON = ("package.json",)

# Overridable only for smoke tests against a scratch prefix.
STATE_PREFIX = os.environ.get("TTP_STATE_PREFIX", "_state/")
MANIFEST_KEY = STATE_PREFIX + "_manifest.json"
SNAPSHOT_PREFIX = os.environ.get("TTP_SNAPSHOT_PREFIX", "_state-snapshots/")
MANIFEST_VERSION = 1

# Never mirrored even if present on disk (interpreter / OS litter).
_IGNORED_NAMES = {"__pycache__", ".DS_Store", "desktop.ini", "Thumbs.db"}
_IGNORED_SUFFIXES = (".pyc", ".pyo")

# Minimum sanity for a push: refuse to mirror a working tree that obviously
# lost its state (failed checkout, wrong cwd) — mirroring it would delete the
# good copy in R2.
MIN_ENTRIES_FILES = 10

CONTENT_TYPES = {
    ".json": "application/json; charset=utf-8",
    ".xml": "application/xml; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gz": "application/gzip",
}


def content_type(path: str) -> str:
    """Content-Type stored on the R2 object (so a public read is typed right)."""
    ext = os.path.splitext(path)[1].lower()
    if ext in CONTENT_TYPES:
        return CONTENT_TYPES[ext]
    guessed, _ = mimetypes.guess_type(path)
    return guessed or "application/octet-stream"


def is_state_path(rel: str) -> bool:
    """True if a repo-relative POSIX path belongs to the persistent state."""
    rel = rel.lstrip("/")
    if rel in STATE_FILES:
        return True
    return any(rel.startswith(d + "/") for d in STATE_DIRS)


def _ignored(rel: str) -> bool:
    parts = rel.split("/")
    return any(p in _IGNORED_NAMES for p in parts) or rel.endswith(_IGNORED_SUFFIXES)


def iter_state_files(root: Path) -> list[str]:
    """Repo-relative POSIX paths of every state file present under `root`."""
    out: list[str] = []
    for f in STATE_FILES:
        if (root / f).is_file():
            out.append(f)
    for d in STATE_DIRS:
        base = root / d
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [n for n in dirnames if n not in _IGNORED_NAMES]
            for name in filenames:
                rel = Path(dirpath, name).relative_to(root).as_posix()
                if not _ignored(rel):
                    out.append(rel)
    return sorted(out)


def slugs_from(channels: list[dict], speakers: list[dict]) -> list[str]:
    seen: list[str] = []
    for rec in list(channels) + list(speakers):
        s = (rec or {}).get("slug")
        if s and s not in seen:
            seen.append(s)
    return seen


def sparse_patterns(channels: list[dict], speakers: list[dict]) -> list[str]:
    """No-cone sparse-checkout patterns selecting the SOURCE tree only.

    Everything is included, then the state and every output of the generator
    is excluded: those must come from R2 (state) or be rebuilt (outputs). Any
    file excluded here that the rebuild does not recreate shows up as
    "missing" in the comparison — which is exactly what we want to learn.
    Slugs of disabled channels are excluded too, on purpose.
    """
    pats = ["/*"]
    pats += [f"!/{d}/" for d in STATE_DIRS]
    pats += [f"!/{f}" for f in STATE_FILES]
    pats += [f"!/{f}" for f in GENERATED_ROOT_FILES]
    pats.append("!/mobile/*.json")
    pats += [f"/mobile/{f}" for f in MOBILE_SOURCE_JSON]
    for slug in slugs_from(channels, speakers):
        for s in dict.fromkeys((slug, slug.lower())):
            pats.append(f"!/{s}.html")
            pats.append(f"!/{s}/")
    return pats


# --------------------------------------------------------------------------
# Hashing / manifest
# --------------------------------------------------------------------------

def md5_file(path: Path) -> tuple[str, int]:
    h = hashlib.md5()
    size = 0
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def build_file_index(root: Path, rels: list[str], workers: int = 16) -> dict[str, dict]:
    index: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(md5_file, root / r): r for r in rels}
        for fut in as_completed(futs):
            md5, size = fut.result()
            index[futs[fut]] = {"md5": md5, "size": size}
    return dict(sorted(index.items()))


def plan_sync(local: dict[str, dict], remote_etags: dict[str, str]) -> tuple[list[str], list[str]]:
    """(to_upload, to_delete) — rel paths. remote_etags: rel -> md5 hex."""
    upload = [r for r, meta in local.items() if remote_etags.get(r) != meta["md5"]]
    delete = [r for r in remote_etags if r not in local]
    return sorted(upload), sorted(delete)


def deletion_allowed(n_delete: int, n_remote: int, max_ratio: float = 0.05, floor: int = 200) -> bool:
    """Mass-deletion guard: a mirror that wants to drop a big share of R2 is
    far more likely a broken working tree than a real change."""
    return n_delete <= max(floor, int(n_remote * max_ratio))


def is_newer(remote: dict | None, commit_time: int, commit: str) -> bool:
    """True if the manifest already in R2 comes from a strictly newer commit."""
    if not remote:
        return False
    if remote.get("commit") == commit:
        return False
    return int(remote.get("commit_time") or 0) > int(commit_time)


# --------------------------------------------------------------------------
# R2 client
# --------------------------------------------------------------------------

def normalize_endpoint(url: str) -> str:
    """scheme://host only.

    The pipeline's R2_ENDPOINT_URL secret ends with `/<bucket>` (that is why
    every MP3 lives under the `thetorahpodcast/` key prefix). PUT/GET survive
    it, but ListObjectsV2 is then routed as a GetObject of the key `<bucket>`
    and fails with NoSuchKey — so the path is dropped here.
    """
    from urllib.parse import urlsplit

    parts = urlsplit(url.strip())
    return f"{parts.scheme}://{parts.netloc}"


def r2_client(workers: int = 32):
    import boto3  # lazy: keeps the pure helpers importable without boto3
    from botocore.config import Config

    missing = [k for k in ("R2_ENDPOINT_URL", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET_NAME")
               if not os.environ.get(k)]
    if missing:
        raise SystemExit(f"missing environment: {', '.join(missing)}")
    cfg = Config(max_pool_connections=workers + 4, retries={"max_attempts": 8, "mode": "adaptive"},
                 s3={"addressing_style": "path"})
    endpoint = normalize_endpoint(os.environ["R2_ENDPOINT_URL"])
    if endpoint != os.environ["R2_ENDPOINT_URL"].rstrip("/"):
        print("note: R2_ENDPOINT_URL carries a path component; it is ignored here "
              "(state keys live at the bucket root, under _state/)")
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
        config=cfg,
    )
    return client, os.environ["R2_BUCKET_NAME"]


def get_manifest(s3, bucket: str) -> dict | None:
    try:
        obj = s3.get_object(Bucket=bucket, Key=MANIFEST_KEY)
    except s3.exceptions.NoSuchKey:
        return None
    except Exception as exc:  # 404 surfaces as ClientError on some endpoints
        if "NoSuchKey" in str(exc) or "404" in str(exc):
            return None
        raise
    return json.loads(obj["Body"].read().decode("utf-8"))


def list_remote(s3, bucket: str) -> dict[str, str]:
    """rel path -> md5 (ETag) for every object under _state/ except the manifest."""
    out: dict[str, str] = {}
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=STATE_PREFIX):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key == MANIFEST_KEY:
                continue
            out[key[len(STATE_PREFIX):]] = obj["ETag"].strip('"')
    return out


def _put_file(s3, bucket: str, root: Path, rel: str, md5: str) -> None:
    body = (root / rel).read_bytes()
    s3.put_object(
        Bucket=bucket,
        Key=STATE_PREFIX + rel,
        Body=body,
        ContentType=content_type(rel),
        ContentMD5=base64.b64encode(bytes.fromhex(md5)).decode(),
        CacheControl="no-cache",
    )


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def _summary(lines: list[str]) -> None:
    text = "\n".join(lines)
    print(text)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")


def _output(**kv) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            for k, v in kv.items():
                fh.write(f"{k}={v}\n")


# --------------------------------------------------------------------------
# push
# --------------------------------------------------------------------------

def cmd_push(args) -> int:
    root = Path(args.root).resolve()
    t0 = time.time()
    commit = args.commit or _git(root, "rev-parse", "HEAD")
    commit_time = int(_git(root, "show", "-s", "--format=%ct", commit))
    # Phase 1 invariant: git is the truth, so the mirrored state must be the
    # committed one. Report (do not fail) if the working tree drifted.
    dirty = _git(root, "status", "--porcelain", "--", *STATE_DIRS, *STATE_FILES)
    rels = iter_state_files(root)
    n_entries = sum(1 for r in rels if r.startswith("feeds/") and r.endswith(".entries.json")
                    and r.count("/") == 1)
    if n_entries < MIN_ENTRIES_FILES or "processed.json" not in rels:
        print(f"::error::refusing to mirror: only {n_entries} feeds/*.entries.json "
              f"(min {MIN_ENTRIES_FILES}), processed.json present={'processed.json' in rels}")
        return 2

    local = build_file_index(root, rels)
    s3, bucket = r2_client(args.workers)

    remote_manifest = get_manifest(s3, bucket)
    if is_newer(remote_manifest, commit_time, commit):
        print(f"::warning::R2 state already mirrors a newer commit "
              f"({remote_manifest.get('commit')}) — skipping this mirror of {commit}.")
        return 0

    remote = list_remote(s3, bucket)
    upload, delete = plan_sync(local, remote)
    up_bytes = sum(local[r]["size"] for r in upload)
    print(f"local={len(local)} remote={len(remote)} upload={len(upload)} "
          f"({up_bytes / 1e6:.1f} MB) delete={len(delete)}")

    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(_put_file, s3, bucket, root, r, local[r]["md5"]): r for r in upload}
        for fut in as_completed(futs):
            try:
                fut.result()
            except Exception as exc:  # keep going, report at the end
                errors.append(f"{futs[fut]}: {exc}")
    if errors:
        print("::error::" + f"{len(errors)} upload(s) failed; manifest NOT updated")
        for e in errors[:20]:
            print("  " + e)
        return 1

    # Re-check right before publishing the manifest (a newer run may have
    # finished in the meantime): never move the manifest backwards.
    latest = get_manifest(s3, bucket)
    if is_newer(latest, commit_time, commit):
        print(f"::warning::a newer mirror ({latest.get('commit')}) landed during this push; "
              "manifest left untouched.")
        return 0

    manifest = {
        "version": MANIFEST_VERSION,
        "commit": commit,
        "commit_time": commit_time,
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "run_id": os.environ.get("GITHUB_RUN_ID", ""),
        "workflow": os.environ.get("GITHUB_WORKFLOW", ""),
        "working_tree_matches_commit": not dirty,
        "count": len(local),
        "bytes": sum(m["size"] for m in local.values()),
        "files": local,
    }
    s3.put_object(Bucket=bucket, Key=MANIFEST_KEY,
                  Body=json.dumps(manifest, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
                  ContentType=content_type(MANIFEST_KEY), CacheControl="no-cache")

    deleted = 0
    if delete:
        if deletion_allowed(len(delete), len(remote)) or args.allow_mass_delete:
            for i in range(0, len(delete), 1000):
                batch = delete[i:i + 1000]
                s3.delete_objects(Bucket=bucket, Delete={
                    "Objects": [{"Key": STATE_PREFIX + r} for r in batch], "Quiet": True})
                deleted += len(batch)
        else:
            print(f"::warning::{len(delete)} stale object(s) in R2 NOT deleted "
                  f"(mass-deletion guard, remote={len(remote)}). Re-run with --allow-mass-delete if intended.")

    snap = ""
    if args.snapshot:
        snap = snapshot(s3, bucket, root, manifest, force=args.force_snapshot)

    _summary([
        "### R2 state mirror",
        f"- commit `{commit}` (working tree matches commit: {not dirty})",
        f"- files {len(local)} / {manifest['bytes'] / 1e6:.1f} MB",
        f"- uploaded {len(upload)} ({up_bytes / 1e6:.1f} MB), deleted {deleted}",
        f"- snapshot: {snap or 'not requested'}",
        f"- duration {time.time() - t0:.0f}s",
    ])
    if dirty:
        print("::warning::state paths differ from the commit in the working tree:\n" + dirty[:2000])
    _output(commit=commit, uploaded=len(upload), deleted=deleted)
    return 0


def snapshot(s3, bucket: str, root: Path, manifest: dict, force: bool = False) -> str:
    """Daily tarball of the state minus transcripts. Idempotent per UTC day."""
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    prefix = f"{SNAPSHOT_PREFIX}{day}/"
    tar_key = prefix + "state.tar.gz"
    if not force:
        try:
            s3.head_object(Bucket=bucket, Key=tar_key)
            return f"{day} already exists"
        except Exception:
            pass
    files = {r: m for r, m in manifest["files"].items()
             if not r.startswith(SNAPSHOT_EXCLUDE_PREFIXES)}
    with tempfile.TemporaryDirectory() as tmp:
        tpath = Path(tmp) / "state.tar.gz"
        with tarfile.open(tpath, "w:gz", compresslevel=6) as tar:
            for rel in files:
                tar.add(root / rel, arcname=rel, recursive=False)
        s3.upload_file(str(tpath), bucket, tar_key,
                       ExtraArgs={"ContentType": "application/gzip"})
        size = tpath.stat().st_size
    snap_manifest = {k: v for k, v in manifest.items() if k != "files"}
    snap_manifest.update({"files": files, "count": len(files),
                          "bytes": sum(m["size"] for m in files.values()),
                          "excluded_prefixes": list(SNAPSHOT_EXCLUDE_PREFIXES),
                          "tar_bytes": size})
    s3.put_object(Bucket=bucket, Key=prefix + "manifest.json",
                  Body=json.dumps(snap_manifest, ensure_ascii=False, separators=(",", ":")).encode(),
                  ContentType=content_type("manifest.json"))
    return f"{day} written ({len(files)} files, {size / 1e6:.1f} MB gz)"


# --------------------------------------------------------------------------
# pull
# --------------------------------------------------------------------------

def _get_file(s3, bucket: str, dest: Path, rel: str) -> str:
    obj = s3.get_object(Bucket=bucket, Key=STATE_PREFIX + rel)
    data = obj["Body"].read()
    out = dest / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return hashlib.md5(data).hexdigest()


def cmd_pull(args) -> int:
    dest = Path(args.dest).resolve()
    t0 = time.time()
    s3, bucket = r2_client(args.workers)
    manifest = get_manifest(s3, bucket)
    if not manifest:
        print("::error::no state manifest in R2 (_state/_manifest.json)")
        return 2
    if args.expect_commit and manifest["commit"] != args.expect_commit:
        print(f"::error::R2 state is at {manifest['commit']}, expected {args.expect_commit}")
        return 3
    todo = list(manifest["files"])
    have: dict[str, str] = {}
    for attempt in range(1, 4):
        bad: list[str] = []
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(_get_file, s3, bucket, dest, r): r for r in todo}
            for fut in as_completed(futs):
                rel = futs[fut]
                try:
                    have[rel] = fut.result()
                except Exception as exc:
                    print(f"  download failed {rel}: {exc}")
                    bad.append(rel)
        mismatched = [r for r in manifest["files"] if have.get(r) != manifest["files"][r]["md5"]]
        if not mismatched:
            break
        # Either transient errors or a mirror landed mid-restore: re-read the
        # manifest and fetch only what disagrees with it.
        fresh = get_manifest(s3, bucket)
        if args.expect_commit and fresh["commit"] != args.expect_commit:
            print(f"::error::state moved to {fresh['commit']} during restore (expected {args.expect_commit})")
            return 3
        if fresh["commit"] != manifest["commit"]:
            print(f"state moved {manifest['commit'][:10]} -> {fresh['commit'][:10]} during restore; resyncing")
            for rel in set(have) - set(fresh["files"]):
                (dest / rel).unlink(missing_ok=True)
                have.pop(rel, None)
            manifest = fresh
            mismatched = [r for r in manifest["files"] if have.get(r) != manifest["files"][r]["md5"]]
        todo = mismatched
        print(f"attempt {attempt}: {len(todo)} file(s) to (re)fetch")
    else:
        print(f"::error::{len(todo)} file(s) still mismatch the manifest after 3 attempts")
        return 1

    _summary([
        "### R2 state restore",
        f"- commit `{manifest['commit']}` (mirrored {manifest.get('created_at')}, run {manifest.get('run_id')})",
        f"- files {len(manifest['files'])} / {manifest.get('bytes', 0) / 1e6:.1f} MB, all MD5-verified",
        f"- duration {time.time() - t0:.0f}s",
    ])
    _output(commit=manifest["commit"])
    return 0


def cmd_manifest(args) -> int:
    s3, bucket = r2_client(4)
    manifest = get_manifest(s3, bucket)
    if not manifest:
        print("::error::no state manifest in R2")
        return 2
    head = {k: v for k, v in manifest.items() if k != "files"}
    print(json.dumps(head, indent=2))
    _output(commit=manifest["commit"], count=manifest.get("count", 0))
    return 0


def cmd_sparse(args) -> int:
    channels = json.loads(Path(args.channels).read_text(encoding="utf-8-sig"))
    speakers = json.loads(Path(args.speakers).read_text(encoding="utf-8-sig")) if args.speakers else []
    print("\n".join(sparse_patterns(channels, speakers)))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("push", help="mirror state from a git working tree to R2")
    sp.add_argument("--root", default=".")
    sp.add_argument("--commit", default="", help="commit the state belongs to (default HEAD)")
    sp.add_argument("--snapshot", action="store_true", help="also write today's snapshot if missing")
    sp.add_argument("--force-snapshot", action="store_true")
    sp.add_argument("--allow-mass-delete", action="store_true")
    sp.add_argument("--workers", type=int, default=32)
    sp.set_defaults(func=cmd_push)

    sl = sub.add_parser("pull", help="restore state from R2 into a directory")
    sl.add_argument("--dest", default=".")
    sl.add_argument("--expect-commit", default="")
    sl.add_argument("--workers", type=int, default=48)
    sl.set_defaults(func=cmd_pull)

    sm = sub.add_parser("manifest", help="print the mirrored manifest header")
    sm.set_defaults(func=cmd_manifest)

    ss = sub.add_parser("sparse-patterns", help="sparse-checkout patterns for a source-only tree")
    ss.add_argument("--channels", required=True)
    ss.add_argument("--speakers", default="")
    ss.set_defaults(func=cmd_sparse)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
