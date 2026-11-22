"""scripts/pipeline_gate.py: a scheduled run stops early only when the last
full run was idle and nothing changed since; any doubt -> full run."""
import hashlib
import io
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import pipeline_gate as pg  # noqa: E402
import site_state  # noqa: E402

NOW = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
CHANNELS = [{"slug": "a", "youtube_channel_id": "UCa"}, {"slug": "b", "youtube_channel_id": "UCb"},
            {"slug": "x", "source": "rss", "rss_url": "https://ext.invalid/rss"},
            {"slug": "off", "youtube_channel_id": "UCo", "enabled": False}]
RECORD = {"version": pg.GATE_VERSION, "recorded_at": (NOW - timedelta(hours=2)).isoformat(),
          "source_sha": "s" * 40, "generation": 7, "manifest_etag": "E7", "idle": True, "not_idle": [],
          "handled": {"a": ["a1", "a2"], "b": ["b1"]}, "rss": {"x": "3:abc"}}
OBS = {"youtube": {"a": ["a1", "a2"], "b": ["b1"]}, "rss": {"x": "3:abc"}, "errors": []}


def never_called(_ids):
    raise AssertionError("no unknown id: the API must not be called")


def d(record=RECORD, observed=OBS, **kw):
    args = dict(event="schedule", head_sha="s" * 40, manifest_etag="E7", now=NOW,
                observed=observed, channels=CHANNELS, processable_fn=never_called)
    args.update(kw)
    return pg.decide(record, **args)


def test_skips_when_nothing_changed():
    run, why = d()
    assert run is False and why.startswith("nothing to do")


@pytest.mark.parametrize("change, expect", [
    (dict(event="workflow_dispatch"), "workflow_dispatch trigger"),
    (dict(record=None), "no gate record"),
    (dict(record={**RECORD, "idle": False, "not_idle": ["backfill backlog 3"]}), "backfill backlog 3"),
    (dict(head_sha="t" * 40), "source changed"),
    (dict(manifest_etag="E8"), "manifest changed"),
    (dict(manifest_etag=""), "manifest changed"),
    (dict(now=NOW + timedelta(hours=13)), "first run of the UTC day"),
    (dict(now=NOW + timedelta(hours=4, minutes=1)), "h ago"),
    (dict(observed=None), "not observed"),
    (dict(observed={**OBS, "errors": ["b: HTTPError 500"]}), "could not check"),
    (dict(observed={**OBS, "youtube": {"a": ["a1", "a2"]}}), "does not cover"),
    (dict(observed={**OBS, "rss": {"x": "4:def"}}), "external feed x changed"),
])
def test_runs_on_any_change_or_doubt(change, expect):
    run, why = d(**change)
    assert run is True and expect in why, why


def test_new_processable_video_runs():
    obs = {**OBS, "youtube": {"a": ["a3", "a1", "a2"], "b": ["b1"]}}
    run, why = d(observed=obs, processable_fn=lambda ids: ["a3"] if "a3" in ids else [])
    assert run is True and "new video(s) on YouTube - a: a3" in why


def test_upcoming_live_alone_does_not_run():
    # In the RSS but not processable yet (live/upcoming): process_podcasts would skip it too.
    obs = {**OBS, "youtube": {"a": ["live1", "a1", "a2"], "b": ["b1"]}}
    seen = []
    run, why = d(observed=obs, processable_fn=lambda ids: seen.extend(ids) or [])
    assert run is False and seen == ["live1"] and "1 not processable yet" in why


def test_handled_ids_and_idle_problems():
    processed = {"a": ["a1"]}
    entries = {"a": [{"video_id": "a2"}], "b": []}
    assert pg.handled_ids({"youtube": {"a": ["a3", "a2", "a1"], "b": ["b1"]}}, processed, entries) == \
        {"a": ["a2", "a1"], "b": []}
    ok = dict(push_unchanged="true", pending="0", index_pending="",
              channel_errors=[], auth_error=False, credit_error=False, observed=OBS)
    assert pg.idle_problems(**ok) == []
    for k, v in [("push_unchanged", "false"), ("push_unchanged", ""), ("pending", "2"),
                 ("index_pending", "true"), ("channel_errors", [{"slug": "a"}]),
                 ("auth_error", True), ("credit_error", True), ("observed", None),
                 ("observed", {**OBS, "errors": ["a: x"]})]:
        assert pg.idle_problems(**{**ok, k: v}), k


class FakeS3:
    def __init__(self):
        self.objs = {}

    def _etag(self, key):
        return '"' + hashlib.md5(self.objs[key]).hexdigest() + '"'

    def get_object(self, Bucket, Key):
        if Key not in self.objs:
            err = Exception("NoSuchKey")
            err.response = {"Error": {"Code": "NoSuchKey"}, "ResponseMetadata": {"HTTPStatusCode": 404}}
            raise err
        return {"Body": io.BytesIO(self.objs[Key]), "ETag": self._etag(Key)}

    def head_object(self, Bucket, Key):
        return {"ETag": self._etag(Key)}

    def put_object(self, Bucket, Key, Body, **_kw):
        self.objs[Key] = Body
        return {"ETag": self._etag(Key)}


@pytest.fixture
def env(tmp_path, monkeypatch):
    fake = FakeS3()
    fake.objs[site_state.MANIFEST_KEY] = b'{"generation": 7}'
    monkeypatch.setattr(site_state, "r2_client", lambda *_a, **_k: (fake, "bucket"))
    monkeypatch.setattr(pg, "_git_head", lambda: "s" * 40)
    monkeypatch.setattr(pg, "observe", lambda _ch: json.loads(json.dumps(OBS)))
    monkeypatch.setattr(pg, "processable", lambda ids: [])
    out = tmp_path / "out.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setenv("TTP_CHANNEL_ERRORS_FILE", str(tmp_path / "ledger.jsonl"))
    (tmp_path / "channels.json").write_text(json.dumps(CHANNELS))
    (tmp_path / "processed.json").write_text(json.dumps({"a": ["a1", "a2"], "b": ["b1"]}))
    (tmp_path / "feeds").mkdir()
    return SimpleNamespace(s3=fake, tmp=tmp_path, out=out, mp=monkeypatch)


def outputs(path):
    return dict(line.split("=", 1) for line in path.read_text().splitlines())


def check(env, event):
    env.mp.setenv("GITHUB_EVENT_NAME", event)
    env.out.write_text("")
    pg.main(["check", "--channels", str(env.tmp / "channels.json"),
             "--observed-out", str(env.tmp / "obs.json")])
    return outputs(env.out)


def record(env, unchanged):
    pg.main(["record", "--root", str(env.tmp), "--observed", str(env.tmp / "obs.json"),
             "--push-unchanged", unchanged, "--pending", "0",
             "--generation", "7"])


def test_roundtrip_full_run_then_skip_then_forced(env, monkeypatch):
    assert check(env, "schedule")["run"] == "true"            # no record yet
    record(env, "false")                                       # that run changed the state
    assert check(env, "schedule")["run"] == "true"            # -> not idle
    record(env, "true")                                        # an idle full run
    rec = json.loads(env.s3.objs[site_state.GATE_KEY])
    assert rec["idle"] is True and rec["handled"] == {"a": ["a1", "a2"], "b": ["b1"]}
    o = check(env, "schedule")
    assert o["run"] == "false" and o["reason"].startswith("nothing to do")
    assert check(env, "workflow_dispatch")["run"] == "true"   # manual dispatch always runs
    env.s3.objs[site_state.MANIFEST_KEY] = b'{"generation": 8}'  # another writer
    assert check(env, "schedule")["run"] == "true"


def test_gate_error_is_a_full_run(env, monkeypatch):
    monkeypatch.setattr(pg, "observe", lambda _ch: 1 / 0)
    o = check(env, "schedule")
    assert o["run"] == "true" and "gate error" in o["reason"]


def test_empty_youtube_feed_is_a_doubt(monkeypatch):
    class R:
        def __init__(self, body):
            self.body = body

        def read(self):
            return self.body

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    monkeypatch.setattr(pg.urllib.request, "urlopen", lambda *_a, **_k: R(b"<feed></feed>"))
    with pytest.raises(ValueError):
        pg.youtube_feed_ids("UCa")
    monkeypatch.setattr(pg.urllib.request, "urlopen",
                        lambda *_a, **_k: R(b"<feed><yt:videoId>v1</yt:videoId></feed>"))
    assert pg.youtube_feed_ids("UCa") == ["v1"]


def test_observation_errors_carry_no_text(monkeypatch):
    def boom(_cid):
        raise RuntimeError("https://www.googleapis.com/?key=SECRET")
    monkeypatch.setattr(pg, "youtube_feed_ids", boom)
    monkeypatch.setattr(pg, "rss_digest", lambda _u: "1:x")
    o = pg.observe(CHANNELS)
    assert o["errors"] == ["a: RuntimeError", "b: RuntimeError"] and "SECRET" not in json.dumps(o)
