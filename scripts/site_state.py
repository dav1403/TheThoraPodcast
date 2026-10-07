"""Persistent pipeline state <-> Cloudflare R2 (`_state/` prefix).

Phase 4 of "stop committing generated artefacts": R2 IS the source of truth of
the state the pipeline re-reads from one run to the next. Git only holds the
source. (Phases 1-3: git was the truth and this module only mirrored it; the
v1 code paths — `push --commit`, `is_newer` — are kept for the transition.)

What counts as state (re-read by the next run, cannot be recomputed from the
source tree alone) is defined ONCE, in STATE_DIRS / STATE_FILES below:

  feeds/        *.entries.json (THE course database — never lose it), *.xml,
                *.channel_info.json, ingest_state.json, rss_health.json,
                transcripts/*.txt (~36 000 files, ~600 MB)
  artwork/      masters + thumb/ (+ thumb/.state.json)
  search-fts/   inverted index + .build-state.json (append-only doc ids)
  processed.json, backfill_state.json

social_state.json deliberately stays in git (tiny, written by other workflows).

Layout in the bucket (prefix overridable: `_state-staging/` off main):
  _state/<path>                         one object per file, ETag == MD5
  _state/_manifest.json                 written LAST, compare-and-swap (If-Match)
                                        v2: generation (monotonic), files md5/size
  _state/_lock.json                     writer lease, created with If-None-Match
  _state-snapshots/YYYY-MM-DD/state.tar.gz        daily, WITHOUT transcripts
  _state-snapshots/YYYY-MM-DD/transcripts.tar.gz  weekly (Sunday UTC)
  _state-snapshots/YYYY-MM-DD/manifest.json

Write protocol (every workflow that changes the state):
  lock acquire -> pull --record R -> work -> push --base-record R -> lock release
The lease serialises writers across workflows (git used to, by refusing a
stale push); the If-Match on the manifest detects a lost update if the lease
was ever broken. Readers (deploy-site, social posts...) just `pull`, MD5-
verified against the manifest, without the lock.

CLI (see --help):
  lock acquire|release|status   writer lease
  push      upload the working tree's state (full, or --only globs = partial)
  pull      restore the state (or --only globs) into a directory, MD5-verified
  manifest  print the manifest header (generation, counts) / GITHUB_OUTPUT
  adopt     turn the v1 manifest (git era) into v2 generation 1, no upload
  seed      server-side copy of another prefix into this one (staging)
  snapshot-restore  rebuild a state directory from a daily snapshot
  prune-snapshots   retention (30 days; 90 for those carrying transcripts)
  cond-test         check that R2 honours If-None-Match / If-Match on PUT
  sparse-patterns   no-cone sparse-checkout patterns for a SOURCE-only checkout

Credentials come from the usual pipeline secrets: R2_ENDPOINT_URL,
R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME. No secret is ever
written into the state or the manifest.
"""

from __future__ import annotations

import argparse
import base64
import fnmatch
import hashlib
import json
import mimetypes
import os
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
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
LOCK_KEY = STATE_PREFIX + "_lock.json"
# Objects under STATE_PREFIX that are not state files.
RESERVED_KEYS = (MANIFEST_KEY, LOCK_KEY)
SNAPSHOT_PREFIX = os.environ.get("TTP_SNAPSHOT_PREFIX", "_state-snapshots/")
MANIFEST_VERSION = 2
SNAPSHOT_KEEP_DAYS = 30
SNAPSHOT_KEEP_DAYS_TRANSCRIPTS = 90
TRANSCRIPTS_PREFIX = "feeds/transcripts/"

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


def generation_of(manifest: dict | None) -> int:
    """Monotonic state generation. v1 manifests (git era) count as 0."""
    if not manifest:
        return 0
    return int(manifest.get("generation") or 0)


def select_only(rels, patterns: list[str]) -> list[str]:
    """Rel paths matching any fnmatch pattern ('*' crosses '/'); all if none."""
    if not patterns:
        return sorted(rels)
    return sorted(r for r in rels if any(fnmatch.fnmatchcase(r, p) for p in patterns))


def is_top_entries(rel: str) -> bool:
    return rel.startswith("feeds/") and rel.endswith(".entries.json") and rel.count("/") == 1


def is_top_xml(rel: str) -> bool:
    return rel.startswith("feeds/") and rel.endswith(".xml") and rel.count("/") == 1


def state_counts(root: Path, rels: list[str]) -> dict:
    """What the anti-loss guard compares: episodes per entries.json, xml feeds."""
    entries: dict[str, int] = {}
    for r in rels:
        if is_top_entries(r):
            try:
                entries[r] = len(json.loads((root / r).read_text(encoding="utf-8-sig") or "[]"))
            except ValueError:
                entries[r] = -1  # unreadable: always a regression
    return {"entries": entries, "xml": sum(1 for r in rels if is_top_xml(r)),
            "processed": "processed.json" in rels}


def loss_problems(base: dict, local: dict, partial: bool = False,
                  allow_removed: tuple[str, ...] = ()) -> list[str]:
    """Reasons to REFUSE a push: the new state would lose episodes or feeds.

    base = counts of the restored generation, local = counts about to be
    pushed. A partial push only carries the files it touched: absent ones are
    kept from the manifest, so only the present ones are compared.
    """
    out: list[str] = []
    be, le = base.get("entries", {}), local.get("entries", {})
    for name, n in sorted(be.items()):
        if name not in le:
            if not partial and name not in allow_removed:
                out.append(f"{name}: would disappear ({n} episodes in the restored state)")
        elif le[name] < n:
            out.append(f"{name}: {le[name]} episodes < {n} in the restored state")
    if not partial:
        if int(local.get("xml", 0)) < int(base.get("xml", 0)):
            out.append(f"feeds/*.xml: {local.get('xml')} < {base.get('xml')} in the restored state")
        if base.get("processed") and not local.get("processed"):
            out.append("processed.json would disappear")
    return out


def lock_expired(lock: dict, now: float) -> bool:
    return float(lock.get("expires_at_epoch") or 0) <= now


def snapshot_days_to_prune(days: dict[str, bool], today: str,
                           keep: int = SNAPSHOT_KEEP_DAYS,
                           keep_transcripts: int = SNAPSHOT_KEEP_DAYS_TRANSCRIPTS) -> list[str]:
    """days: 'YYYY-MM-DD' -> carries transcripts. Returns the days to delete.

    Never deletes the newest snapshot, nor the newest one carrying
    transcripts, whatever their age (a stalled pipeline must not end up with
    no backup at all)."""
    from datetime import date

    t = date.fromisoformat(today)
    valid = {}
    for d, tr in days.items():
        try:
            valid[d] = (date.fromisoformat(d), tr)
        except ValueError:
            continue
    if not valid:
        return []
    newest = max(valid)
    newest_tr = max((d for d, (_x, tr) in valid.items() if tr), default=None)
    out = []
    for d, (dd, tr) in sorted(valid.items()):
        if d in (newest, newest_tr):
            continue
        age = (t - dd).days
        if age > (keep_transcripts if tr else keep):
            out.append(d)
    return out


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


def _err_code(exc: Exception) -> str:
    resp = getattr(exc, "response", None) or {}
    code = str((resp.get("Error") or {}).get("Code") or "")
    status = str((resp.get("ResponseMetadata") or {}).get("HTTPStatusCode") or "")
    return code or status


def _is_precondition_failed(exc: Exception) -> bool:
    return _err_code(exc) in ("PreconditionFailed", "412")


def _is_conflict(exc: Exception) -> bool:
    # R2 answers 409 ConditionalRequestConflict to two racing conditional PUTs.
    return _err_code(exc) in ("ConditionalRequestConflict", "409", "OperationAborted")


def _is_missing(exc: Exception) -> bool:
    return _err_code(exc) in ("NoSuchKey", "404", "NotFound") or "NoSuchKey" in str(exc)


def get_json_with_etag(s3, bucket: str, key: str) -> tuple[dict | None, str]:
    """(parsed body, ETag) of a JSON object, (None, '') if absent."""
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
    except Exception as exc:
        if _is_missing(exc):
            return None, ""
        raise
    return json.loads(obj["Body"].read().decode("utf-8")), obj.get("ETag", "")


def get_manifest(s3, bucket: str) -> dict | None:
    return get_json_with_etag(s3, bucket, MANIFEST_KEY)[0]


def get_manifest_etag(s3, bucket: str) -> tuple[dict | None, str]:
    return get_json_with_etag(s3, bucket, MANIFEST_KEY)


def put_json(s3, bucket: str, key: str, body: dict, *, if_match: str = "",
             if_none_match: bool = False, cache_control: str = "no-cache") -> str | None:
    """Conditional PUT of a JSON object. Returns the new ETag, None on 412/409."""
    kw = {}
    if if_match:
        kw["IfMatch"] = if_match
    if if_none_match:
        kw["IfNoneMatch"] = "*"
    try:
        resp = s3.put_object(Bucket=bucket, Key=key, ContentType=content_type(key),
                             CacheControl=cache_control,
                             Body=json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
                             **kw)
    except Exception as exc:
        if _is_precondition_failed(exc) or _is_conflict(exc):
            return None
        raise
    return resp.get("ETag", "")


def list_remote(s3, bucket: str) -> dict[str, str]:
    """rel path -> md5 (ETag) for every state object under the prefix."""
    out: dict[str, str] = {}
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=STATE_PREFIX):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key in RESERVED_KEYS:
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


def _git_or(root: Path, default: str, *args: str) -> str:
    try:
        return _git(root, *args)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return default


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


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# --------------------------------------------------------------------------
# lock (writer lease)
# --------------------------------------------------------------------------

def _lock_token(args) -> str:
    return getattr(args, "token", "") or os.environ.get("TTP_LOCK_TOKEN", "")


def lock_check(s3, bucket: str, token: str) -> str:
    """'' if `token` holds a live lease, else the reason it does not."""
    lock, _etag = get_json_with_etag(s3, bucket, LOCK_KEY)
    if not lock:
        return "no lease in R2 (released or never taken)"
    if lock.get("token") != token:
        return f"lease held by someone else ({lock.get('owner')})"
    if lock_expired(lock, time.time()):
        return f"our lease expired at {lock.get('expires_at')}"
    return ""


def cmd_lock(args) -> int:
    s3, bucket = r2_client(4)
    if args.action == "status":
        lock, etag = get_json_with_etag(s3, bucket, LOCK_KEY)
        print(json.dumps({"lock": lock, "etag": etag,
                          "expired": bool(lock) and lock_expired(lock, time.time())}, indent=1))
        return 0
    if args.action == "release":
        token = _lock_token(args)
        lock, _etag = get_json_with_etag(s3, bucket, LOCK_KEY)
        if not lock:
            print("lease already free")
            return 0
        if lock.get("token") != token:
            print(f"::warning::lease not ours (held by {lock.get('owner')}): left in place")
            return 0
        s3.delete_object(Bucket=bucket, Key=LOCK_KEY)
        print(f"lease released ({lock.get('owner')})")
        return 0

    # acquire
    owner = args.owner or "{} run {}".format(os.environ.get("GITHUB_WORKFLOW", "local"),
                                             os.environ.get("GITHUB_RUN_ID", "-"))
    token = uuid.uuid4().hex
    deadline = time.time() + args.wait_minutes * 60
    waited_since = time.time()
    last_holder = ""
    while True:
        now = time.time()
        body = {"token": token, "owner": owner, "run_id": os.environ.get("GITHUB_RUN_ID", ""),
                "workflow": os.environ.get("GITHUB_WORKFLOW", ""), "acquired_at": _now_iso(),
                "expires_at_epoch": int(now + args.ttl_minutes * 60),
                "expires_at": datetime.fromtimestamp(now + args.ttl_minutes * 60, timezone.utc)
                .replace(microsecond=0).isoformat()}
        if put_json(s3, bucket, LOCK_KEY, body, if_none_match=True) is not None:
            break
        lock, etag = get_json_with_etag(s3, bucket, LOCK_KEY)
        if lock and lock_expired(lock, now):
            print(f"::warning::taking over an EXPIRED lease of {lock.get('owner')} "
                  f"(expired {lock.get('expires_at')})")
            if put_json(s3, bucket, LOCK_KEY, body, if_match=etag) is not None:
                break
            continue
        holder = (lock or {}).get("owner", "?")
        if holder != last_holder:
            print(f"lease held by {holder} until {(lock or {}).get('expires_at')}; waiting")
            last_holder = holder
        if now >= deadline:
            print(f"::error::could not take the state lease in {args.wait_minutes} min "
                  f"(held by {holder})")
            return 1
        time.sleep(args.poll_seconds)
    waited = time.time() - waited_since
    print(f"lease taken by {owner} (waited {waited:.0f}s, ttl {args.ttl_minutes} min)")
    _output(token=token, waited_seconds=int(waited))
    env = os.environ.get("GITHUB_ENV")
    if env:
        # Not an access secret (anyone able to write R2 holds the keys), but
        # no reason to print it in the public logs of the next steps either.
        print(f"::add-mask::{token}")
        with open(env, "a", encoding="utf-8") as fh:
            fh.write(f"TTP_LOCK_TOKEN={token}\n")
    if args.print_token:
        print(f"token={token}")
    return 0


# --------------------------------------------------------------------------
# push
# --------------------------------------------------------------------------

def _local_index(root: Path, only: list[str], workers: int = 16) -> dict[str, dict]:
    rels = select_only(iter_state_files(root), only)
    return build_file_index(root, rels, workers)


def cmd_push(args) -> int:
    root = Path(args.root).resolve()
    t0 = time.time()
    only = args.only or []
    partial = bool(only)
    source_commit = args.commit or _git_or(root, "", "rev-parse", "HEAD")
    source_time = int(_git_or(root, "0", "show", "-s", "--format=%ct", source_commit)) if source_commit else 0

    base: dict | None = None
    if args.base_record:
        base = json.loads(Path(args.base_record).read_text(encoding="utf-8"))
        if base.get("only") and not partial:
            print("::error::the base record comes from a partial pull: a full push would drop "
                  "everything that was not restored")
            return 2
    elif not args.init:
        print("::error::push needs --base-record (written by `pull --record`), or --init to "
              "create a first generation")
        return 2

    local = _local_index(root, only, args.workers)
    rels = list(local)
    if not partial:
        n_entries = sum(1 for r in rels if is_top_entries(r))
        if n_entries < MIN_ENTRIES_FILES or "processed.json" not in rels:
            print(f"::error::refusing to push: only {n_entries} feeds/*.entries.json "
                  f"(min {MIN_ENTRIES_FILES}), processed.json present={'processed.json' in rels}")
            return 2
    elif not local:
        print(f"nothing matches {only}: no push")
        return 0

    counts = state_counts(root, rels)
    if base is not None:
        problems = loss_problems(base.get("counts") or {}, counts, partial=partial,
                                 allow_removed=tuple(args.allow_removed or ()))
        if problems and not args.allow_loss:
            print("::error::anti-loss guard: the state about to be pushed loses data; NOTHING uploaded")
            for pb in problems[:30]:
                print("  " + pb)
            return 5

    s3, bucket = r2_client(args.workers)
    token = _lock_token(args)
    if not args.no_lock:
        why = lock_check(s3, bucket, token)
        if why:
            print(f"::error::not holding the state lease: {why}; NOTHING uploaded")
            return 6

    remote_manifest, etag = get_manifest_etag(s3, bucket)
    if base is not None and etag != base.get("etag"):
        print(f"::error::the manifest changed since this run restored the state "
              f"(generation {base.get('generation')} -> {generation_of(remote_manifest)}): "
              "lost-update refused, NOTHING uploaded")
        return 4
    if args.init and remote_manifest is not None and not args.force_init:
        print("::error::--init on a prefix that already has a manifest (use --force-init)")
        return 2

    remote_files = dict((remote_manifest or {}).get("files") or {})
    if partial:
        upload = sorted(r for r, m in local.items() if (remote_files.get(r) or {}).get("md5") != m["md5"])
        delete: list[str] = []
        files = dict(remote_files)
        files.update(local)
        files = dict(sorted(files.items()))
        n_remote = len(remote_files)
    else:
        remote = list_remote(s3, bucket)
        upload, delete = plan_sync(local, remote)
        files = local
        n_remote = len(remote)
    up_bytes = sum(local[r]["size"] for r in upload)
    if partial and not upload:
        print(f"partial push: nothing changed in {only}: manifest untouched (generation stays "
              f"{generation_of(remote_manifest)})")
        _output(generation=generation_of(remote_manifest), uploaded=0, deleted=0, etag=etag)
        return 0
    print(f"{'partial ' if partial else ''}push: local={len(local)} remote={n_remote} "
          f"upload={len(upload)} ({up_bytes / 1e6:.1f} MB) delete={len(delete)}")

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

    if not args.no_lock:
        why = lock_check(s3, bucket, token)
        if why:
            print(f"::error::lease lost during the upload ({why}); manifest NOT updated")
            return 6

    gen = generation_of(remote_manifest) + 1
    manifest = {
        "version": MANIFEST_VERSION,
        "generation": gen,
        "parent_generation": generation_of(remote_manifest),
        # `commit` kept for v1 readers: it is now the SOURCE commit the writer
        # ran on, not a commit that contains the state.
        "commit": source_commit,
        "commit_time": source_time,
        "source_commit": source_commit,
        "created_at": _now_iso(),
        "run_id": os.environ.get("GITHUB_RUN_ID", ""),
        "workflow": os.environ.get("GITHUB_WORKFLOW", ""),
        "partial": partial,
        "only": only,
        "count": len(files),
        "bytes": sum(m["size"] for m in files.values()),
        "files": files,
    }
    new_etag = put_json(s3, bucket, MANIFEST_KEY, manifest,
                        if_match=etag if remote_manifest is not None else "",
                        if_none_match=remote_manifest is None)
    if new_etag is None:
        print("::error::compare-and-swap of the manifest failed (412): another writer published "
              "a generation meanwhile. Objects uploaded by this run may now disagree with the "
              "manifest; readers detect it (MD5) and the next writer re-uploads them.")
        return 4

    deleted = 0
    if delete:
        if deletion_allowed(len(delete), n_remote) or args.allow_mass_delete:
            for i in range(0, len(delete), 1000):
                batch = delete[i:i + 1000]
                s3.delete_objects(Bucket=bucket, Delete={
                    "Objects": [{"Key": STATE_PREFIX + r} for r in batch], "Quiet": True})
                deleted += len(batch)
        else:
            print(f"::warning::{len(delete)} stale object(s) in R2 NOT deleted "
                  f"(mass-deletion guard, remote={n_remote}). Re-run with --allow-mass-delete if intended.")

    snap = ""
    if args.snapshot and partial:
        snap = "skipped (partial push: the working tree does not hold the whole state)"
    elif args.snapshot:
        snap = snapshot(s3, bucket, root, manifest, force=args.force_snapshot,
                        with_transcripts=args.with_transcripts, copy_to=args.snapshot_copy)

    _summary([
        "### R2 state push",
        f"- generation {gen} (parent {manifest['parent_generation']}), source commit `{source_commit[:10]}`",
        f"- {'partial (' + ', '.join(only) + ')' if partial else 'full'}: "
        f"files {len(files)} / {manifest['bytes'] / 1e6:.1f} MB",
        f"- uploaded {len(upload)} ({up_bytes / 1e6:.1f} MB), deleted {deleted}",
        f"- entries.json {len(counts['entries'])} / episodes {sum(v for v in counts['entries'].values() if v > 0)}"
        f" / xml {counts['xml']}",
        f"- snapshot: {snap or 'not requested'}",
        f"- duration {time.time() - t0:.0f}s",
    ])
    _output(generation=gen, uploaded=len(upload), deleted=deleted, etag=new_etag)
    return 0


def _transcripts_snapshot_due(s3, bucket: str, today: str) -> bool:
    """Weekly: Sunday UTC, or no transcripts tarball in the last 7 days."""
    from datetime import date

    if date.fromisoformat(today).weekday() == 6:
        return True
    days = list_snapshot_days(s3, bucket)
    t = date.fromisoformat(today)
    recent = [d for d, tr in days.items() if tr and 0 <= (t - date.fromisoformat(d)).days < 7]
    return not recent


def snapshot(s3, bucket: str, root: Path, manifest: dict, force: bool = False,
             with_transcripts: str = "auto", copy_to: str = "") -> str:
    """Daily tarball of the state minus transcripts (+ weekly transcripts).
    Idempotent per UTC day."""
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
    tr_files = {r: m for r, m in manifest["files"].items() if r.startswith(TRANSCRIPTS_PREFIX)}
    do_tr = with_transcripts == "yes" or (with_transcripts == "auto" and _transcripts_snapshot_due(s3, bucket, day))
    msg = ""
    with tempfile.TemporaryDirectory() as tmp:
        tpath = Path(tmp) / "state.tar.gz"
        with tarfile.open(tpath, "w:gz", compresslevel=6) as tar:
            for rel in files:
                tar.add(root / rel, arcname=rel, recursive=False)
        s3.upload_file(str(tpath), bucket, tar_key,
                       ExtraArgs={"ContentType": "application/gzip"})
        size = tpath.stat().st_size
        if copy_to:
            Path(copy_to).parent.mkdir(parents=True, exist_ok=True)
            Path(copy_to).write_bytes(tpath.read_bytes())
        tr_size = 0
        if do_tr and tr_files:
            trpath = Path(tmp) / "transcripts.tar.gz"
            with tarfile.open(trpath, "w:gz", compresslevel=6) as tar:
                for rel in tr_files:
                    tar.add(root / rel, arcname=rel, recursive=False)
            s3.upload_file(str(trpath), bucket, prefix + "transcripts.tar.gz",
                           ExtraArgs={"ContentType": "application/gzip"})
            tr_size = trpath.stat().st_size
            msg = f", transcripts {len(tr_files)} files {tr_size / 1e6:.1f} MB gz"
    snap_manifest = {k: v for k, v in manifest.items() if k != "files"}
    snap_manifest.update({"files": files, "count": len(files),
                          "bytes": sum(m["size"] for m in files.values()),
                          "excluded_prefixes": list(SNAPSHOT_EXCLUDE_PREFIXES),
                          "tar_bytes": size, "day": day})
    if do_tr and tr_files:
        snap_manifest.update({"transcripts": tr_files, "transcripts_tar_bytes": tr_size})
    s3.put_object(Bucket=bucket, Key=prefix + "manifest.json",
                  Body=json.dumps(snap_manifest, ensure_ascii=False, separators=(",", ":")).encode(),
                  ContentType=content_type("manifest.json"))
    return f"{day} written ({len(files)} files, {size / 1e6:.1f} MB gz{msg})"


def list_snapshot_days(s3, bucket: str) -> dict[str, bool]:
    """'YYYY-MM-DD' -> carries a transcripts tarball."""
    days: dict[str, bool] = {}
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=SNAPSHOT_PREFIX):
        for obj in page.get("Contents", []):
            rest = obj["Key"][len(SNAPSHOT_PREFIX):]
            if "/" not in rest:
                continue
            day, name = rest.split("/", 1)
            days.setdefault(day, False)
            if name == "transcripts.tar.gz":
                days[day] = True
    return days


def cmd_prune(args) -> int:
    s3, bucket = r2_client(8)
    days = list_snapshot_days(s3, bucket)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    doomed = snapshot_days_to_prune(days, today, args.keep_days, args.keep_days_transcripts)
    print(f"snapshots: {len(days)} day(s), {sum(days.values())} with transcripts; prune {len(doomed)}: {doomed}")
    if args.dry_run:
        return 0
    for d in doomed:
        keys = [o["Key"] for page in s3.get_paginator("list_objects_v2").paginate(
            Bucket=bucket, Prefix=f"{SNAPSHOT_PREFIX}{d}/") for o in page.get("Contents", [])]
        if keys:
            s3.delete_objects(Bucket=bucket, Delete={"Objects": [{"Key": k} for k in keys], "Quiet": True})
    _output(pruned=len(doomed), kept=len(days) - len(doomed))
    return 0


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


def write_record(path: str, manifest: dict, etag: str, dest: Path, rels: list[str], only: list[str]) -> None:
    rec = {"generation": generation_of(manifest), "etag": etag, "commit": manifest.get("commit", ""),
           "created_at": manifest.get("created_at", ""), "only": only,
           "counts": state_counts(dest, rels), "prefix": STATE_PREFIX}
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(rec, indent=1), encoding="utf-8")


def cmd_pull(args) -> int:
    dest = Path(args.dest).resolve()
    t0 = time.time()
    only = args.only or []
    s3, bucket = r2_client(args.workers)
    manifest, etag = get_manifest_etag(s3, bucket)
    if not manifest:
        print(f"::error::no state manifest in R2 ({MANIFEST_KEY})")
        return 2
    if args.expect_commit and manifest["commit"] != args.expect_commit:
        print(f"::error::R2 state is at {manifest['commit']}, expected {args.expect_commit}")
        return 3
    if args.expect_generation and generation_of(manifest) != args.expect_generation:
        print(f"::error::R2 state is at generation {generation_of(manifest)}, expected {args.expect_generation}")
        return 3

    def wanted(m: dict) -> list[str]:
        return select_only(m["files"], only)

    todo = wanted(manifest)
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
        mismatched = [r for r in wanted(manifest) if have.get(r) != manifest["files"][r]["md5"]]
        if not mismatched:
            break
        # Either transient errors or a writer published mid-restore: re-read
        # the manifest and fetch only what disagrees with it.
        fresh, fresh_etag = get_manifest_etag(s3, bucket)
        if args.expect_commit and fresh["commit"] != args.expect_commit:
            print(f"::error::state moved to {fresh['commit']} during restore (expected {args.expect_commit})")
            return 3
        if args.expect_generation and generation_of(fresh) != args.expect_generation:
            print(f"::error::state moved to generation {generation_of(fresh)} during restore")
            return 3
        if fresh_etag != etag:
            print(f"state moved generation {generation_of(manifest)} -> {generation_of(fresh)} during restore; resyncing")
            for rel in set(have) - set(wanted(fresh)):
                (dest / rel).unlink(missing_ok=True)
                have.pop(rel, None)
            manifest, etag = fresh, fresh_etag
            mismatched = [r for r in wanted(manifest) if have.get(r) != manifest["files"][r]["md5"]]
        todo = mismatched
        print(f"attempt {attempt}: {len(todo)} file(s) to (re)fetch")
    else:
        print(f"::error::{len(todo)} file(s) still mismatch the manifest after 3 attempts")
        return 1

    rels = wanted(manifest)
    if args.record:
        write_record(args.record, manifest, etag, dest, rels, only)
    _summary([
        "### R2 state restore",
        f"- generation {generation_of(manifest)} (source `{manifest.get('commit', '')[:10]}`, "
        f"written {manifest.get('created_at')}, run {manifest.get('run_id')})",
        f"- {'only ' + ', '.join(only) + ': ' if only else ''}files {len(rels)}, all MD5-verified",
        f"- duration {time.time() - t0:.0f}s",
    ])
    _output(commit=manifest.get("commit", ""), generation=generation_of(manifest), etag=etag)
    return 0


def cmd_manifest(args) -> int:
    s3, bucket = r2_client(4)
    manifest, etag = get_manifest_etag(s3, bucket)
    if not manifest:
        print("::error::no state manifest in R2")
        return 2
    head = {k: v for k, v in manifest.items() if k != "files"}
    head["etag"] = etag
    print(json.dumps(head, indent=2))
    _output(commit=manifest.get("commit", ""), count=manifest.get("count", 0),
            commit_time=manifest.get("commit_time", 0), generation=generation_of(manifest),
            created_at=manifest.get("created_at", ""), etag=etag)
    return 0


def cmd_adopt(args) -> int:
    """v1 (git-era) manifest -> v2 generation 1, same files, no upload.

    Run once at the production cutover, right after the last git-era mirror:
    the objects already match, only the ordering key changes."""
    s3, bucket = r2_client(4)
    manifest, etag = get_manifest_etag(s3, bucket)
    if not manifest:
        print("::error::no manifest to adopt")
        return 2
    if generation_of(manifest) > 0:
        print(f"already v2 (generation {generation_of(manifest)}): nothing to do")
        return 0
    if args.expect_commit and manifest.get("commit") != args.expect_commit:
        print(f"::error::manifest mirrors {manifest.get('commit')}, expected {args.expect_commit}")
        return 3
    m2 = dict(manifest)
    m2.update({"version": MANIFEST_VERSION, "generation": 1, "parent_generation": 0,
               "source_commit": manifest.get("commit", ""), "adopted_at": _now_iso(),
               "adopted_from_version": manifest.get("version", 1)})
    if put_json(s3, bucket, MANIFEST_KEY, m2, if_match=etag) is None:
        print("::error::manifest changed during adopt (412)")
        return 4
    print(f"adopted: generation 1, commit {m2.get('commit')}, {m2.get('count')} files")
    return 0


def cmd_seed(args) -> int:
    """Server-side copy of another state prefix into STATE_PREFIX (staging)."""
    src = args.from_prefix
    if src == STATE_PREFIX:
        print("::error::source and destination prefixes are the same")
        return 2
    s3, bucket = r2_client(args.workers)
    src_manifest, _ = get_json_with_etag(s3, bucket, src + "_manifest.json")
    if not src_manifest:
        print(f"::error::no manifest under {src}")
        return 2
    cur, cur_etag = get_manifest_etag(s3, bucket)
    if cur and not args.force:
        print(f"::error::{STATE_PREFIX} already has a manifest (generation {generation_of(cur)}); use --force")
        return 2
    files = src_manifest["files"]

    def cp(rel: str) -> None:
        s3.copy_object(Bucket=bucket, Key=STATE_PREFIX + rel,
                       CopySource={"Bucket": bucket, "Key": src + rel},
                       MetadataDirective="COPY")

    errors: list[str] = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(cp, r): r for r in files}
        for fut in as_completed(futs):
            try:
                fut.result()
            except Exception as exc:
                errors.append(f"{futs[fut]}: {exc}")
    if errors:
        print(f"::error::{len(errors)} copy error(s): {errors[:5]}")
        return 1
    m = dict(src_manifest)
    m.update({"version": MANIFEST_VERSION, "generation": max(1, generation_of(src_manifest)),
              "parent_generation": generation_of(src_manifest), "seeded_from": src,
              "seeded_at": _now_iso(), "source_commit": src_manifest.get("commit", "")})
    if put_json(s3, bucket, MANIFEST_KEY, m, if_match=cur_etag if cur else "",
                if_none_match=cur is None) is None:
        print("::error::manifest write refused (412)")
        return 4
    print(f"seeded {len(files)} object(s) {src} -> {STATE_PREFIX} in {time.time() - t0:.0f}s, "
          f"generation {m['generation']}")
    return 0


def _safe_extract(tar: tarfile.TarFile, dest: Path) -> list[str]:
    names = []
    for mem in tar.getmembers():
        if not mem.isfile():
            continue
        p = Path(mem.name)
        if p.is_absolute() or ".." in p.parts:
            raise ValueError(f"unsafe path in snapshot: {mem.name}")
        tar.extract(mem, dest)
        names.append(p.as_posix())
    return names


def cmd_snapshot_restore(args) -> int:
    """Rebuild a state directory from a daily snapshot, MD5-verified.

    Transcripts come from the newest transcripts tarball at or before that
    day. The result is pushed with `push --init` (empty prefix) or
    `push --base-record` (replace a live prefix, under the lease)."""
    s3, bucket = r2_client(8)
    days = list_snapshot_days(s3, bucket)
    if not days:
        print("::error::no snapshot")
        return 2
    day = args.day if args.day != "latest" else max(days)
    if day not in days:
        print(f"::error::no snapshot for {day} (have {sorted(days)[-5:]})")
        return 2
    dest = Path(args.dest).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    snap = json.loads(s3.get_object(Bucket=bucket, Key=f"{SNAPSHOT_PREFIX}{day}/manifest.json")["Body"].read())
    want: dict[str, dict] = dict(snap["files"])
    with tempfile.TemporaryDirectory() as tmp:
        tp = Path(tmp) / "state.tar.gz"
        s3.download_file(bucket, f"{SNAPSHOT_PREFIX}{day}/state.tar.gz", str(tp))
        with tarfile.open(tp, "r:gz") as tar:
            _safe_extract(tar, dest)
        tr_day = ""
        if args.transcripts:
            cands = sorted(d for d, tr in days.items() if tr and d <= day)
            if cands:
                tr_day = cands[-1]
                trm = snap if tr_day == day else json.loads(s3.get_object(
                    Bucket=bucket, Key=f"{SNAPSHOT_PREFIX}{tr_day}/manifest.json")["Body"].read())
                want.update(trm.get("transcripts") or {})
                tp2 = Path(tmp) / "transcripts.tar.gz"
                s3.download_file(bucket, f"{SNAPSHOT_PREFIX}{tr_day}/transcripts.tar.gz", str(tp2))
                with tarfile.open(tp2, "r:gz") as tar:
                    _safe_extract(tar, dest)
            else:
                print("::warning::no transcripts tarball at or before this day")
    bad = []
    for rel, meta in want.items():
        f = dest / rel
        if not f.is_file() or md5_file(f)[0] != meta["md5"]:
            bad.append(rel)
    _summary([
        "### Snapshot restore",
        f"- snapshot {day} (generation {snap.get('generation', 0)}, commit `{snap.get('commit', '')[:10]}`)",
        f"- transcripts from {tr_day or 'none'}",
        f"- {len(want)} file(s) expected, {len(want) - len(bad)} MD5-verified, {len(bad)} bad",
        f"- duration {time.time() - t0:.0f}s",
    ])
    if bad:
        print(f"::error::{len(bad)} file(s) missing or corrupt after restore: {bad[:10]}")
        return 1
    _output(day=day, files=len(want), transcripts_day=tr_day)
    return 0


def cmd_cond_test(args) -> int:
    """Empirical check of R2 conditional writes on a scratch key (deleted after)."""
    s3, bucket = r2_client(2)
    key = f"{STATE_PREFIX}_condtest/{uuid.uuid4().hex}.json"
    res = {}
    e1 = put_json(s3, bucket, key, {"n": 1}, if_none_match=True)
    res["create If-None-Match on absent key"] = "OK" if e1 else "REFUSED"
    e2 = put_json(s3, bucket, key, {"n": 2}, if_none_match=True)
    res["second If-None-Match on existing key"] = "REFUSED (412/409)" if e2 is None else "ACCEPTED"
    e3 = put_json(s3, bucket, key, {"n": 3}, if_match='"0123456789abcdef0123456789abcdef"')
    res["If-Match with a stale ETag"] = "REFUSED (412/409)" if e3 is None else "ACCEPTED"
    e4 = put_json(s3, bucket, key, {"n": 4}, if_match=e1 or "")
    res["If-Match with the current ETag"] = "OK" if e4 else "REFUSED"
    body, _ = get_json_with_etag(s3, bucket, key)
    res["final body"] = body
    s3.delete_object(Bucket=bucket, Key=key)
    import botocore
    res["botocore"] = botocore.__version__
    print(json.dumps(res, indent=1))
    ok = (e1 and e2 is None and e3 is None and e4 and body == {"n": 4})
    _summary(["### R2 conditional writes", *[f"- {k}: {v}" for k, v in res.items()],
              f"- verdict: {'SUPPORTED' if ok else 'NOT RELIABLE'}"])
    return 0 if ok else 1


def cmd_sparse(args) -> int:
    channels = json.loads(Path(args.channels).read_text(encoding="utf-8-sig"))
    speakers = json.loads(Path(args.speakers).read_text(encoding="utf-8-sig")) if args.speakers else []
    print("\n".join(sparse_patterns(channels, speakers)))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    sk = sub.add_parser("lock", help="writer lease: acquire / release / status")
    sk.add_argument("action", choices=("acquire", "release", "status"))
    sk.add_argument("--owner", default="")
    sk.add_argument("--token", default="", help="release: token (default $TTP_LOCK_TOKEN)")
    sk.add_argument("--ttl-minutes", type=int, default=200)
    sk.add_argument("--wait-minutes", type=int, default=60)
    sk.add_argument("--poll-seconds", type=int, default=30)
    sk.add_argument("--print-token", action="store_true")
    sk.set_defaults(func=cmd_lock)

    sp = sub.add_parser("push", help="upload the state of a working tree (full or --only)")
    sp.add_argument("--root", default=".")
    sp.add_argument("--commit", default="", help="source commit the writer ran on (default HEAD)")
    sp.add_argument("--base-record", default="", help="record written by `pull --record` (CAS + anti-loss)")
    sp.add_argument("--init", action="store_true", help="first generation of an empty prefix")
    sp.add_argument("--force-init", action="store_true")
    sp.add_argument("--only", action="append", help="partial push: fnmatch glob(s) of rel paths")
    sp.add_argument("--token", default="", help="lease token (default $TTP_LOCK_TOKEN)")
    sp.add_argument("--no-lock", action="store_true", help="tests / seeding only")
    sp.add_argument("--allow-loss", action="store_true", help="bypass the anti-loss guard (manual)")
    sp.add_argument("--allow-removed", action="append", help="entries.json allowed to disappear")
    sp.add_argument("--snapshot", action="store_true", help="also write today's snapshot if missing")
    sp.add_argument("--force-snapshot", action="store_true")
    sp.add_argument("--with-transcripts", choices=("auto", "yes", "no"), default="auto")
    sp.add_argument("--snapshot-copy", default="", help="also keep the day's tarball at this path")
    sp.add_argument("--allow-mass-delete", action="store_true")
    sp.add_argument("--workers", type=int, default=32)
    sp.set_defaults(func=cmd_push)

    sl = sub.add_parser("pull", help="restore the state into a directory")
    sl.add_argument("--dest", default=".")
    sl.add_argument("--only", action="append", help="fnmatch glob(s) of rel paths")
    sl.add_argument("--record", default="", help="write the restore record (for push --base-record)")
    sl.add_argument("--expect-commit", default="")
    sl.add_argument("--expect-generation", type=int, default=0)
    sl.add_argument("--workers", type=int, default=48)
    sl.set_defaults(func=cmd_pull)

    sm = sub.add_parser("manifest", help="print the manifest header")
    sm.set_defaults(func=cmd_manifest)

    sa = sub.add_parser("adopt", help="v1 manifest -> v2 generation 1 (cutover)")
    sa.add_argument("--expect-commit", default="")
    sa.set_defaults(func=cmd_adopt)

    se = sub.add_parser("seed", help="server-side copy of another prefix into this one")
    se.add_argument("--from-prefix", required=True)
    se.add_argument("--force", action="store_true")
    se.add_argument("--workers", type=int, default=48)
    se.set_defaults(func=cmd_seed)

    sr = sub.add_parser("snapshot-restore", help="rebuild a state directory from a snapshot")
    sr.add_argument("--day", default="latest")
    sr.add_argument("--dest", required=True)
    sr.add_argument("--transcripts", action="store_true")
    sr.set_defaults(func=cmd_snapshot_restore)

    sq = sub.add_parser("prune-snapshots", help="snapshot retention")
    sq.add_argument("--keep-days", type=int, default=SNAPSHOT_KEEP_DAYS)
    sq.add_argument("--keep-days-transcripts", type=int, default=SNAPSHOT_KEEP_DAYS_TRANSCRIPTS)
    sq.add_argument("--dry-run", action="store_true")
    sq.set_defaults(func=cmd_prune)

    sc = sub.add_parser("cond-test", help="check R2 conditional PUT support")
    sc.set_defaults(func=cmd_cond_test)

    ss = sub.add_parser("sparse-patterns", help="sparse-checkout patterns for a source-only tree")
    ss.add_argument("--channels", required=True)
    ss.add_argument("--speakers", default="")
    ss.set_defaults(func=cmd_sparse)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
