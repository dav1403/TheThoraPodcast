"""Phase 4 protocol of scripts/site_state.py against an in-memory fake S3.

Covers what replaces git's serialisation: the writer lease (If-None-Match),
the manifest compare-and-swap (If-Match), generations, partial push/pull, the
anti-loss guard, snapshots (+ weekly transcripts), snapshot restore, retention.
The real R2 behaviour of the conditional headers is checked separately by
`site_state.py cond-test` in CI (staging).
"""
import hashlib
import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import site_state  # noqa: E402


class _Err(Exception):
    def __init__(self, code, status):
        super().__init__(code)
        self.response = {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}}


class FakeS3:
    def __init__(self):
        self.objs: dict[str, bytes] = {}
        self.gets: list[str] = []  # every GetObject key (R2 Class B reads)

    def _etag(self, key):
        return '"' + hashlib.md5(self.objs[key]).hexdigest() + '"'

    def state_gets(self):
        """Rel paths of the per-file state GETs (manifest/lock excluded)."""
        return sorted(k[len(site_state.STATE_PREFIX):] for k in self.gets
                      if k.startswith(site_state.STATE_PREFIX) and k not in site_state.RESERVED_KEYS)

    def get_object(self, Bucket, Key):
        self.gets.append(Key)
        if Key not in self.objs:
            raise _Err("NoSuchKey", 404)
        return {"Body": io.BytesIO(self.objs[Key]), "ETag": self._etag(Key)}

    def head_object(self, Bucket, Key):
        if Key not in self.objs:
            raise _Err("404", 404)
        return {"ETag": self._etag(Key)}

    def put_object(self, Bucket, Key, Body, IfMatch=None, IfNoneMatch=None, **_kw):
        if IfNoneMatch == "*" and Key in self.objs:
            raise _Err("PreconditionFailed", 412)
        if IfMatch is not None and (Key not in self.objs or self._etag(Key) != IfMatch):
            raise _Err("PreconditionFailed", 412)
        self.objs[Key] = Body if isinstance(Body, bytes) else Body.encode()
        return {"ETag": self._etag(Key)}

    def delete_object(self, Bucket, Key):
        self.objs.pop(Key, None)

    def delete_objects(self, Bucket, Delete):
        for o in Delete["Objects"]:
            self.objs.pop(o["Key"], None)

    def upload_file(self, path, Bucket, Key, ExtraArgs=None):
        self.objs[Key] = Path(path).read_bytes()

    def download_file(self, Bucket, Key, path):
        Path(path).write_bytes(self.objs[Key])

    def copy_object(self, Bucket, Key, CopySource, **_kw):
        self.objs[Key] = self.objs[CopySource["Key"]]

    def get_paginator(self, _name):
        fake = self

        class P:
            def paginate(self, Bucket, Prefix):
                yield {"Contents": [{"Key": k, "ETag": fake._etag(k)}
                                    for k in sorted(fake.objs) if k.startswith(Prefix)]}
        return P()


@pytest.fixture
def s3(monkeypatch):
    fake = FakeS3()
    monkeypatch.setattr(site_state, "r2_client", lambda *_a, **_k: (fake, "bucket"))
    monkeypatch.delenv("TTP_LOCK_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_ENV", raising=False)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    return fake


def make_state(root: Path, n_channels=12, eps=3, transcripts=2):
    (root / "feeds" / "transcripts").mkdir(parents=True, exist_ok=True)
    for i in range(n_channels):
        (root / "feeds" / f"c{i}.entries.json").write_text(json.dumps([{"i": k} for k in range(eps)]))
        (root / "feeds" / f"c{i}.xml").write_text(f"<rss>{i}</rss>")
    for t in range(transcripts):
        (root / "feeds" / "transcripts" / f"v{t}.txt").write_text(f"transcript {t}")
    (root / "artwork").mkdir(exist_ok=True)
    (root / "artwork" / "a.png").write_bytes(b"png")
    (root / "processed.json").write_text("{}")
    (root / "backfill_state.json").write_text("{}")


def run(*argv):
    return site_state.main([str(a) for a in argv])


def token_of(capsys):
    out = capsys.readouterr().out
    return next(line.split("=", 1)[1] for line in out.splitlines() if line.startswith("token="))


def init(tmp_path, s3):
    src = tmp_path / "src"
    make_state(src)
    assert run("push", "--root", src, "--init", "--no-lock", "--commit", "abc") == 0
    return src


def test_init_then_generation_increments(tmp_path, s3, capsys):
    init(tmp_path, s3)
    m = json.loads(s3.objs[site_state.MANIFEST_KEY])
    assert m["generation"] == 1 and m["version"] == 2 and m["count"] == 29
    # a full write cycle: lease, restore with record, change, push
    assert run("lock", "acquire", "--print-token", "--wait-minutes", 0) == 0
    tok = token_of(capsys)
    work = tmp_path / "w"
    assert run("pull", "--dest", work, "--record", tmp_path / "rec.json") == 0
    (work / "feeds" / "c0.entries.json").write_text(json.dumps([{"i": k} for k in range(5)]))
    assert run("push", "--root", work, "--base-record", tmp_path / "rec.json", "--token", tok) == 0
    m = json.loads(s3.objs[site_state.MANIFEST_KEY])
    assert m["generation"] == 2 and m["parent_generation"] == 1
    assert run("lock", "release", "--token", tok) == 0
    assert site_state.LOCK_KEY not in s3.objs


def test_lease_is_exclusive_and_expiry_takeover(tmp_path, s3, capsys, monkeypatch):
    assert run("lock", "acquire", "--print-token", "--wait-minutes", 0, "--ttl-minutes", 5) == 0
    tok1 = token_of(capsys)
    # second writer cannot take it (no wait)
    assert run("lock", "acquire", "--wait-minutes", 0) == 1
    # release by a stranger is a no-op
    assert run("lock", "release", "--token", "nope") == 0
    assert site_state.LOCK_KEY in s3.objs
    # once expired, it is taken over
    now = site_state.time.time()
    monkeypatch.setattr(site_state.time, "time", lambda: now + 3600)
    assert run("lock", "acquire", "--print-token", "--wait-minutes", 0) == 0
    tok2 = token_of(capsys)
    assert tok2 != tok1
    assert json.loads(s3.objs[site_state.LOCK_KEY])["token"] == tok2


def test_push_without_lease_or_with_stale_record_is_refused(tmp_path, s3, capsys):
    init(tmp_path, s3)
    work = tmp_path / "w"
    assert run("pull", "--dest", work, "--record", tmp_path / "rec.json") == 0
    # no lease
    assert run("push", "--root", work, "--base-record", tmp_path / "rec.json") == 6
    assert run("lock", "acquire", "--print-token", "--wait-minutes", 0) == 0
    tok = token_of(capsys)
    # someone else published a generation after our restore (lease broken)
    other = tmp_path / "o"
    assert run("pull", "--dest", other, "--record", tmp_path / "rec2.json") == 0
    (other / "feeds" / "c1.xml").write_text("<rss>other</rss>")
    assert run("push", "--root", other, "--base-record", tmp_path / "rec2.json", "--token", tok) == 0
    before = dict(s3.objs)
    (work / "feeds" / "c2.xml").write_text("<rss>mine</rss>")
    assert run("push", "--root", work, "--base-record", tmp_path / "rec.json", "--token", tok) == 4
    # nothing of ours was uploaded (refused before any object write)
    assert s3.objs == before


def test_anti_loss_guard(tmp_path, s3, capsys):
    init(tmp_path, s3)
    assert run("lock", "acquire", "--print-token", "--wait-minutes", 0) == 0
    tok = token_of(capsys)
    work = tmp_path / "w"
    assert run("pull", "--dest", work, "--record", tmp_path / "rec.json") == 0
    before = dict(s3.objs)
    (work / "feeds" / "c3.entries.json").write_text(json.dumps([{"i": 0}]))  # 3 -> 1
    assert run("push", "--root", work, "--base-record", tmp_path / "rec.json", "--token", tok) == 5
    (work / "feeds" / "c3.entries.json").write_text(json.dumps([{"i": k} for k in range(3)]))
    (work / "feeds" / "c4.xml").unlink()
    (work / "feeds" / "c4.entries.json").unlink()
    assert run("push", "--root", work, "--base-record", tmp_path / "rec.json", "--token", tok) == 5
    assert s3.objs == before


def test_partial_pull_push_keeps_the_rest(tmp_path, s3, capsys):
    init(tmp_path, s3)
    assert run("lock", "acquire", "--print-token", "--wait-minutes", 0) == 0
    tok = token_of(capsys)
    work = tmp_path / "w"
    assert run("pull", "--dest", work, "--only", "feeds/*.entries.json",
               "--record", tmp_path / "rec.json") == 0
    assert not (work / "processed.json").exists() and not (work / "feeds" / "c0.xml").exists()
    (work / "feeds" / "c5.entries.json").write_text(json.dumps([{"i": k, "d": 1} for k in range(3)]))
    assert run("push", "--root", work, "--base-record", tmp_path / "rec.json", "--token", tok,
               "--only", "feeds/*.entries.json") == 0
    m = json.loads(s3.objs[site_state.MANIFEST_KEY])
    assert m["generation"] == 2 and m["count"] == 29 and "processed.json" in m["files"]
    assert site_state.STATE_PREFIX + "processed.json" in s3.objs
    # a FULL push from a partial restore would drop everything else: refused
    assert run("push", "--root", work, "--base-record", tmp_path / "rec.json", "--token", tok) == 2


def test_snapshot_restore_roundtrip_and_prune(tmp_path, s3, capsys):
    src = tmp_path / "src"
    make_state(src)
    assert run("push", "--root", src, "--init", "--no-lock", "--snapshot",
               "--with-transcripts", "yes") == 0
    out = tmp_path / "restored"
    assert run("snapshot-restore", "--dest", out, "--transcripts") == 0
    for f in site_state.iter_state_files(src):
        assert (out / f).read_bytes() == (src / f).read_bytes(), f
    days = {"2026-08-01": False, "2026-09-01": True, "2026-09-30": False, "2026-10-06": True,
            "2026-10-07": False}
    assert site_state.snapshot_days_to_prune(days, "2026-10-07") == ["2026-08-01"]
    assert site_state.snapshot_days_to_prune({"2026-01-01": False}, "2026-10-07") == []


def test_adopt_v1_manifest(tmp_path, s3):
    v1 = {"version": 1, "commit": "c" * 40, "commit_time": 5, "files": {}, "count": 0}
    s3.objs[site_state.MANIFEST_KEY] = json.dumps(v1).encode()
    assert run("adopt", "--expect-commit", "c" * 40) == 0
    m = json.loads(s3.objs[site_state.MANIFEST_KEY])
    assert m["generation"] == 1 and m["version"] == 2 and m["commit"] == "c" * 40
    assert run("adopt") == 0  # idempotent


def test_loss_problems_pure():
    base = {"entries": {"feeds/a.entries.json": 3, "feeds/b.entries.json": 2}, "xml": 2, "processed": True}
    assert site_state.loss_problems(base, base) == []
    assert site_state.loss_problems(base, {"entries": {"feeds/a.entries.json": 3}, "xml": 2,
                                           "processed": True})
    assert site_state.loss_problems(base, {"entries": {"feeds/a.entries.json": 3}, "xml": 1},
                                    partial=True) == []
    assert site_state.select_only(["feeds/a.entries.json", "feeds/transcripts/x.txt", "p.json"],
                                  ["feeds/*.entries.json"]) == ["feeds/a.entries.json"]


# --------------------------------------------------------------------------
# Incremental pull + snapshot seed (R2 Class B reads: 20.5 M/month, 11/2026)
# --------------------------------------------------------------------------

def _write_cycle(tmp_path, s3, capsys, mutate, name="w2"):
    """lease -> pull -> mutate(work) -> push -> release: a new generation."""
    assert run("lock", "acquire", "--print-token", "--wait-minutes", 0) == 0
    tok = token_of(capsys)
    work = tmp_path / name
    rec = tmp_path / f"{name}.json"
    assert run("pull", "--dest", work, "--record", rec, "--no-seed") == 0
    mutate(work)
    assert run("push", "--root", work, "--base-record", rec, "--token", tok) == 0
    assert run("lock", "release", "--token", tok) == 0


def _assert_mirror(s3, dest: Path):
    m = json.loads(s3.objs[site_state.MANIFEST_KEY])
    assert site_state.iter_state_files(dest) == sorted(m["files"])
    for rel, meta in m["files"].items():
        assert hashlib.md5((dest / rel).read_bytes()).hexdigest() == meta["md5"], rel


def test_incremental_pull_only_fetches_what_differs(tmp_path, s3, capsys):
    init(tmp_path, s3)
    work = tmp_path / "w"
    assert run("pull", "--dest", work) == 0
    assert len(s3.state_gets()) == 29  # first restore: everything
    # another writer changes one file in R2
    _write_cycle(tmp_path, s3, capsys,
                 lambda w: (w / "feeds" / "c2.entries.json").write_text(json.dumps([{"i": k} for k in range(4)])))
    # locally: one file corrupted, one missing, one extra state file, one extra
    # non-state file (must survive)
    (work / "feeds" / "c0.xml").write_text("<rss>corrupt</rss>")
    (work / "feeds" / "c1.xml").unlink()
    (work / "feeds" / "zz.entries.json").write_text("[]")
    (work / "feeds" / "transcripts" / "gone.txt").write_text("old")
    (work / "README.md").write_text("source")
    s3.gets.clear()
    capsys.readouterr()
    assert run("pull", "--dest", work, "--record", tmp_path / "rec.json") == 0
    out = capsys.readouterr().out
    # identical files are NOT re-downloaded; modified/missing/changed are
    assert s3.state_gets() == ["feeds/c0.xml", "feeds/c1.xml", "feeds/c2.entries.json"]
    assert "downloaded 3 " in out and "already present 26" in out and "removed 2 " in out
    assert not (work / "feeds" / "zz.entries.json").exists()
    assert not (work / "feeds" / "transcripts" / "gone.txt").exists()
    assert (work / "README.md").read_text() == "source"
    _assert_mirror(s3, work)
    rec = json.loads((tmp_path / "rec.json").read_text())
    assert "feeds/zz.entries.json" not in rec["counts"]["entries"]
    # a second pull of an up-to-date tree reads no state object at all
    s3.gets.clear()
    assert run("pull", "--dest", work) == 0
    assert s3.state_gets() == []


def test_partial_pull_prunes_only_inside_its_globs(tmp_path, s3):
    init(tmp_path, s3)
    work = tmp_path / "w"
    (work / "feeds").mkdir(parents=True)
    (work / "artwork").mkdir()
    (work / "feeds" / "zz.entries.json").write_text("[]")
    (work / "artwork" / "old.png").write_bytes(b"x")
    assert run("pull", "--dest", work, "--only", "feeds/*.entries.json") == 0
    assert not (work / "feeds" / "zz.entries.json").exists()
    assert (work / "artwork" / "old.png").exists()  # outside the globs: untouched
    assert not any(k.startswith(site_state.SNAPSHOT_PREFIX) for k in s3.gets)  # never seeds


def test_empty_runner_is_seeded_from_the_snapshots(tmp_path, s3, capsys):
    src = tmp_path / "src"
    make_state(src, transcripts=5)
    assert run("push", "--root", src, "--init", "--no-lock", "--snapshot", "--with-transcripts", "yes") == 0

    def mutate(w):
        (w / "feeds" / "c3.xml").write_text("<rss>changed after the snapshot</rss>")
        (w / "feeds" / "transcripts" / "v1.txt").unlink()        # deleted after the snapshot
        (w / "feeds" / "transcripts" / "new.txt").write_text("new transcript")
    _write_cycle(tmp_path, s3, capsys, mutate)
    assert site_state.STATE_PREFIX + "feeds/transcripts/v1.txt" not in s3.objs

    s3.gets.clear()
    capsys.readouterr()
    fresh = tmp_path / "runner"
    assert run("pull", "--dest", fresh, "--record", tmp_path / "rec.json") == 0
    out = capsys.readouterr().out
    # 2 tarballs + only what changed since the snapshot
    tars = [k for k in s3.gets if k.startswith(site_state.SNAPSHOT_PREFIX)]
    assert sorted(t.rsplit("/", 1)[1] for t in tars) == ["state.tar.gz", "transcripts.tar.gz"]
    assert s3.state_gets() == ["feeds/c3.xml", "feeds/transcripts/new.txt"]
    assert "removed 0 " in out and "seed: " in out
    # the file deleted since the snapshot is NOT resurrected by the seed
    assert not (fresh / "feeds" / "transcripts" / "v1.txt").exists()
    _assert_mirror(s3, fresh)


def test_broken_snapshot_falls_back_to_a_plain_pull(tmp_path, s3, capsys):
    src = tmp_path / "src"
    make_state(src)
    assert run("push", "--root", src, "--init", "--no-lock", "--snapshot", "--with-transcripts", "yes") == 0
    for k in list(s3.objs):
        if k.endswith("state.tar.gz"):
            s3.objs[k] = b"not a tarball"
        elif k.endswith("transcripts.tar.gz"):
            del s3.objs[k]
    s3.gets.clear()
    fresh = tmp_path / "runner"
    assert run("pull", "--dest", fresh) == 0
    assert "::warning::state seed" in capsys.readouterr().out
    assert len(s3.state_gets()) == 29
    _assert_mirror(s3, fresh)
    # --no-seed never touches the snapshots
    s3.gets.clear()
    assert run("pull", "--dest", tmp_path / "other", "--no-seed") == 0
    assert not any(k.startswith(site_state.SNAPSHOT_PREFIX) for k in s3.gets)


def test_seed_never_overwrites_a_file_already_on_disk(tmp_path, s3):
    src = tmp_path / "src"
    make_state(src)
    assert run("push", "--root", src, "--init", "--no-lock", "--snapshot", "--with-transcripts", "yes") == 0
    dest = tmp_path / "d"
    (dest / "feeds").mkdir(parents=True)
    (dest / "feeds" / "c0.xml").write_text("<rss>local</rss>")
    wanted = set(json.loads(s3.objs[site_state.MANIFEST_KEY])["files"])
    msg = site_state.seed_from_snapshots(s3, "bucket", dest, wanted)
    assert "state.tar.gz" in msg
    assert (dest / "feeds" / "c0.xml").read_text() == "<rss>local</rss>"
    assert (dest / "feeds" / "c1.xml").read_text() == "<rss>1</rss>"
