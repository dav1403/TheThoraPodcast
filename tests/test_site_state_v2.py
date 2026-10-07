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

    def _etag(self, key):
        return '"' + hashlib.md5(self.objs[key]).hexdigest() + '"'

    def get_object(self, Bucket, Key):
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
