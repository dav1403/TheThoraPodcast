"""Legacy orphan pages <-> Cloudflare R2 (`_static-legacy/` prefix).

Phase 3 of moving generated artefacts out of git (Pages in Actions mode).
The legacy orphans are HTML pages the generator no longer produces (old
episode filenames, renamed titles) but that were served while Pages published
`main` as is. They stay online, frozen. Their list is versioned in
scripts/legacy_orphans.txt; their CONTENT lives in R2 so that the site can be
built without them being in git (phase 4).

Layout in the bucket:
  _static-legacy/<path>           one object per page, ETag == MD5
  _static-legacy/_manifest.json   {"files": {path: {"md5", "size", "listed"}}}

CLI:
  sync   upload to R2 every listed page (and --extra ones) that R2 lacks,
         reading the bytes from a git commit (blobless-friendly)
  pull   download every listed page into a site directory, MD5-verified

Credentials: R2_ENDPOINT_URL, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY,
R2_BUCKET_NAME (same as scripts/site_state.py).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import site_state  # noqa: E402

PREFIX = os.environ.get("TTP_LEGACY_PREFIX", "_static-legacy/")
MANIFEST_KEY = PREFIX + "_manifest.json"
DEFAULT_LIST = Path(__file__).resolve().parent / "legacy_orphans.txt"


# --------------------------------------------------------------------------
# Pure helpers (unit-tested)
# --------------------------------------------------------------------------

def parse_list(text: str) -> list[str]:
    """Paths of a list file: one per line, '#' comments and blanks ignored."""
    out: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("/") or ".." in line.split("/") or not line.endswith(".html"):
            raise ValueError(f"invalid legacy path: {line!r}")
        out.append(line)
    if len(out) != len(set(out)):
        raise ValueError("duplicate path in legacy list")
    return out


def plan_sync(wanted: list[str], manifest_files: dict[str, dict]) -> list[str]:
    """Paths to upload: wanted but absent from the R2 manifest."""
    return sorted(p for p in dict.fromkeys(wanted) if p not in manifest_files)


# --------------------------------------------------------------------------
# R2 / git plumbing
# --------------------------------------------------------------------------

def get_manifest(s3, bucket: str) -> dict:
    try:
        obj = s3.get_object(Bucket=bucket, Key=MANIFEST_KEY)
    except Exception as exc:
        if "NoSuchKey" in str(exc) or "404" in str(exc):
            return {"files": {}}
        raise
    return json.loads(obj["Body"].read().decode("utf-8"))


def put_manifest(s3, bucket: str, manifest: dict) -> None:
    manifest["updated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    manifest["count"] = len(manifest["files"])
    s3.put_object(Bucket=bucket, Key=MANIFEST_KEY,
                  Body=json.dumps(manifest, ensure_ascii=False, indent=0, sort_keys=True).encode("utf-8"),
                  ContentType="application/json; charset=utf-8", CacheControl="no-cache")


def git_blob_shas(repo: Path, ref: str, paths: list[str]) -> dict[str, str]:
    """path -> blob sha at `ref`, via ls-tree on explicit paths (trees only)."""
    out: dict[str, str] = {}
    for i in range(0, len(paths), 200):
        chunk = paths[i:i + 200]
        raw = subprocess.run(["git", "-C", str(repo), "ls-tree", "-z", "--full-tree", ref, "--", *chunk],
                             check=True, capture_output=True).stdout
        for rec in raw.split(b"\0"):
            if not rec:
                continue
            meta, path = rec.split(b"\t", 1)
            _mode, typ, sha = meta.split(b" ")
            if typ == b"blob":
                out[path.decode("utf-8", "surrogateescape")] = sha.decode()
    return out


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def _extra_paths(args) -> list[str]:
    extra: list[str] = []
    if args.extra_from_report and Path(args.extra_from_report).is_file():
        rep = json.loads(Path(args.extra_from_report).read_text(encoding="utf-8"))
        extra = list(rep.get("legacy_kept") or [])
    return extra


def cmd_sync(args) -> int:
    listed = parse_list(Path(args.list).read_text(encoding="utf-8"))
    extra = [p for p in _extra_paths(args) if p not in set(listed)]
    s3, bucket = site_state.r2_client(16)
    manifest = get_manifest(s3, bucket)
    files = manifest.setdefault("files", {})
    todo = plan_sync(listed + extra, files)
    print(f"legacy pages: listed={len(listed)} extra={len(extra)} in_r2={len(files)} to_upload={len(todo)}")
    if extra:
        print("::warning::" + f"{len(extra)} new orphan page(s) not in scripts/legacy_orphans.txt "
              "(stored in R2 all the same; add them to the list before phase 4): " + ", ".join(extra[:10]))
    if not todo:
        return 0
    repo = Path(args.repo).resolve()
    shas = git_blob_shas(repo, args.ref, todo)
    absent = [p for p in todo if p not in shas]
    if absent:
        print(f"::error::{len(absent)} legacy page(s) neither in R2 nor in git {args.ref}: {absent[:10]}")
        return 1
    # Hydrate the blobs in one round-trip (partial clone), like site_compare.
    import site_compare
    site_compare.prefetch_blobs(repo, [shas[p] for p in todo])
    extra_set = set(extra)

    def upload(p: str) -> tuple[str, dict]:
        data = site_compare.read_blob(repo, shas[p])
        md5 = hashlib.md5(data).hexdigest()
        s3.put_object(Bucket=bucket, Key=PREFIX + p, Body=data,
                      ContentType="text/html; charset=utf-8",
                      ContentMD5=base64.b64encode(bytes.fromhex(md5)).decode())
        return p, {"md5": md5, "size": len(data), "git_blob": shas[p], "listed": p not in extra_set}

    with ThreadPoolExecutor(max_workers=16) as ex:
        for fut in as_completed([ex.submit(upload, p) for p in todo]):
            p, meta = fut.result()
            files[p] = meta
    manifest["source_ref"] = args.ref
    put_manifest(s3, bucket, manifest)
    print(f"uploaded {len(todo)} legacy page(s) to R2 {PREFIX}")
    return 0


def cmd_pull(args) -> int:
    listed = parse_list(Path(args.list).read_text(encoding="utf-8"))
    dest = Path(args.dest).resolve()
    s3, bucket = site_state.r2_client(32)
    files = get_manifest(s3, bucket).get("files", {})
    absent = [p for p in listed if p not in files]
    if absent:
        print(f"::error::{len(absent)} listed legacy page(s) missing from R2 manifest: {absent[:10]}")
        return 1

    def fetch(p: str) -> str | None:
        data = s3.get_object(Bucket=bucket, Key=PREFIX + p)["Body"].read()
        if hashlib.md5(data).hexdigest() != files[p]["md5"]:
            return f"md5 mismatch {p}"
        out = dest / p
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        return None

    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=32) as ex:
        for fut in as_completed([ex.submit(fetch, p) for p in listed]):
            try:
                err = fut.result()
            except Exception as exc:
                err = str(exc)
            if err:
                errors.append(err)
    if errors:
        print(f"::error::{len(errors)} legacy page(s) failed: {errors[:10]}")
        return 1
    print(f"restored {len(listed)} legacy page(s) from R2 {PREFIX}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    ss = sub.add_parser("sync", help="seed R2 with the listed pages it lacks, from git")
    ss.add_argument("--list", default=str(DEFAULT_LIST))
    ss.add_argument("--repo", required=True, help="git repo holding --ref (blobless OK)")
    ss.add_argument("--ref", required=True)
    ss.add_argument("--extra-from-report", default="",
                    help="site_compare report.json: also store its legacy_kept pages")
    ss.set_defaults(func=cmd_sync)
    sp = sub.add_parser("pull", help="restore the listed pages into a site directory")
    sp.add_argument("--list", default=str(DEFAULT_LIST))
    sp.add_argument("--dest", required=True)
    sp.set_defaults(func=cmd_pull)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
