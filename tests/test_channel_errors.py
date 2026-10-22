"""A failing channel must not block the publication of the others.

Incident 21-22/10/2026: one channel (rav-meir-david-shmueli) raised in
process_podcasts.py, the script exited 1, and GitHub skipped every later step
of "Update Podcast Feeds" (backfill, pages, R2 state push, snapshot) for 6 h.

Contract covered here:
  * process_podcasts.main() keeps going after a channel error, records it in
    the ledger (scripts/channel_errors.py) and returns normally (exit 0), so
    the later steps — the R2 state push included — run;
  * `channel_errors.py check` exits 1 when the ledger is not empty (red run,
    no green lie) and 0 when it is empty;
  * in the workflow, the check is the LAST step and runs `if: always()`, and
    nothing between the process step and the R2 push can be skipped by a
    channel error any more.

Stdlib only, like the rest of the CI suite: process_podcasts.py imports
yt-dlp / feedgen / lxml / boto3, which the tests workflow does not install,
so those modules are stubbed for the import (main() never touches them once
process_channel is replaced).
"""
import importlib
import io
import json
import re
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
WORKFLOW = ROOT / ".github" / "workflows" / "repair_missing_audio.yml"
sys.path.insert(0, str(SCRIPTS))

channel_errors = importlib.import_module("channel_errors")


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------

@pytest.fixture
def ledger(tmp_path, monkeypatch):
    path = tmp_path / "channel_errors.jsonl"
    monkeypatch.setenv(channel_errors.ENV_VAR, str(path))
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    return path


def test_empty_ledger_is_a_clean_run(ledger):
    out = io.StringIO()
    assert not ledger.exists()
    assert channel_errors.check(out=out) == 0
    assert "No channel error" in out.getvalue()
    assert "::error" not in out.getvalue()


def test_recorded_errors_turn_the_check_red(ledger):
    n = channel_errors.record([("rav-a", "Image file must be png or jpg"),
                               ("rav-b", "boom")], step="process_podcasts")
    assert n == 2
    rows = channel_errors.load()
    assert [r["slug"] for r in rows] == ["rav-a", "rav-b"]
    assert rows[0] == {"step": "process_podcasts", "slug": "rav-a",
                       "error": "Image file must be png or jpg"}
    out = io.StringIO()
    assert channel_errors.check(out=out) == 1
    text = out.getvalue()
    assert text.count("::error title=") == 2
    assert "rav-a: Image file must be png or jpg" in text


def test_record_appends_across_calls(ledger):
    channel_errors.record([("rav-a", "x")], step="process_podcasts")
    channel_errors.record([("rav-b", "y")], step="backfill")
    assert [(r["step"], r["slug"]) for r in channel_errors.load()] == [
        ("process_podcasts", "rav-a"), ("backfill", "rav-b")]


def test_record_nothing_writes_nothing(ledger):
    assert channel_errors.record([], step="process_podcasts") == 0
    assert not ledger.exists()


def test_reset_clears_a_stale_ledger(ledger):
    channel_errors.record([("rav-a", "x")], step="process_podcasts")
    channel_errors.reset()
    assert not ledger.exists()
    channel_errors.reset()  # idempotent on a missing file
    assert channel_errors.check(out=io.StringIO()) == 0


def test_corrupt_ledger_line_is_not_a_clean_run(ledger):
    ledger.write_text("not json\n\n", encoding="utf-8")
    rows = channel_errors.load()
    assert len(rows) == 1 and "unreadable ledger line" in rows[0]["error"]
    assert channel_errors.check(out=io.StringIO()) == 1


def test_annotation_escapes_newlines_and_percent(ledger):
    channel_errors.record([("rav-a", "line1\nline2 100%")], step="process_podcasts")
    out = io.StringIO()
    channel_errors.check(out=out)
    annotation = [l for l in out.getvalue().splitlines() if l.startswith("::error")]
    assert len(annotation) == 1  # one command, not split across lines
    assert "line1%0Aline2 100%25" in annotation[0]


def test_long_error_is_truncated(ledger):
    channel_errors.record([("rav-a", "x" * 5000)], step="process_podcasts")
    assert len(channel_errors.load()[0]["error"]) == channel_errors.MAX_ERROR_CHARS


def test_summary_table(ledger, tmp_path):
    summary = tmp_path / "summary.md"
    channel_errors.record([("rav-a", "bad | pipe")], step="process_podcasts")
    assert channel_errors.check(out=io.StringIO(), summary_path=summary) == 1
    md = summary.read_text(encoding="utf-8")
    assert "1 channel error(s)" in md
    assert "| process_podcasts | `rav-a` | bad \\| pipe |" in md


def test_cli_exit_codes(ledger):
    assert channel_errors.main(["check"]) == 0
    channel_errors.record([("rav-a", "x")], step="process_podcasts")
    assert channel_errors.main(["check"]) == 1
    assert channel_errors.main(["reset"]) == 0
    assert channel_errors.main(["check"]) == 0
    assert channel_errors.main([]) == 2


# ---------------------------------------------------------------------------
# process_podcasts.main(): one channel in error, the others still processed
# ---------------------------------------------------------------------------

def _stub_ingest_runtime(monkeypatch):
    """Stub the heavy imports of process_podcasts.py (not installed in CI)."""
    def mod(name, **attrs):
        m = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(m, k, v)
        monkeypatch.setitem(sys.modules, name, m)
        return m

    class _Any:
        def __init__(self, *a, **k):
            pass

    mod("yt_dlp", YoutubeDL=_Any)
    mod("feedgen")
    mod("feedgen.feed", FeedGenerator=_Any)
    mod("feedgen.ext")
    mod("feedgen.ext.base", BaseExtension=_Any)
    mod("lxml", etree=types.SimpleNamespace())
    mod("lxml.etree")
    mod("boto3", client=lambda *a, **k: None)
    mod("botocore")
    mod("botocore.config", Config=_Any)
    mod("botocore.exceptions", ClientError=type("ClientError", (Exception,), {}))
    try:
        import requests  # noqa: F401  (installed by tests.yml)
    except ImportError:
        mod("requests")


@pytest.fixture
def pp(tmp_path, monkeypatch, ledger):
    _stub_ingest_runtime(monkeypatch)
    monkeypatch.setenv("YOUTUBE_API_KEY", "test")
    monkeypatch.setenv("GITHUB_TOKEN", "test")
    monkeypatch.setenv("GITHUB_REPO", "owner/repo")
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)  # the module creates feeds/ and tmp_audio/ in the CWD
    monkeypatch.delitem(sys.modules, "process_podcasts", raising=False)
    module = importlib.import_module("process_podcasts")
    monkeypatch.setitem(sys.modules, "process_podcasts", module)
    monkeypatch.setattr(module, "BACKFILL_BUDGET_FILE", tmp_path / "backfill_budget")
    monkeypatch.setattr(module, "AUTH_FAILED", False)
    channels = [
        {"slug": "rav-first", "youtube_channel_id": "UC1"},
        {"slug": "rav-meir-david-shmueli", "youtube_channel_id": "UC2"},
        {"slug": "rav-after", "youtube_channel_id": "UC3"},
        {"slug": "rss-one", "source": "rss"},
    ]
    (work / "channels.json").write_text(json.dumps(channels), encoding="utf-8")
    return module


def test_one_channel_error_does_not_stop_the_others(pp, monkeypatch, tmp_path):
    called = []

    def fake_process_channel(ch, processed, budget):
        called.append(ch["slug"])
        if ch["slug"] == "rav-meir-david-shmueli":
            raise ValueError("Image file must be png or jpg")
        return 1

    monkeypatch.setattr(pp, "process_channel", fake_process_channel)
    monkeypatch.setattr(sys, "argv", ["process_podcasts.py", "--budget", "10"])

    pp.main()  # must return normally: no SystemExit, so later steps run

    assert called == ["rav-first", "rav-meir-david-shmueli", "rav-after"]
    assert (tmp_path / "backfill_budget").read_text() == "8"
    rows = channel_errors.load()
    assert rows == [{"step": "process_podcasts", "slug": "rav-meir-david-shmueli",
                     "error": "Image file must be png or jpg"}]
    assert channel_errors.check(out=io.StringIO()) == 1  # the run still ends red


def test_clean_run_leaves_an_empty_ledger(pp, monkeypatch):
    channel_errors.record([("stale", "from an earlier run")], step="process_podcasts")
    monkeypatch.setattr(pp, "process_channel", lambda ch, processed, budget: 0)
    monkeypatch.setattr(sys, "argv", ["process_podcasts.py", "--budget", "5"])
    pp.main()
    assert channel_errors.load() == []  # reset at start, nothing recorded
    assert channel_errors.check(out=io.StringIO()) == 0


def test_auth_error_still_stops_the_run_without_ledger_entry(pp, monkeypatch):
    called = []

    def fake_process_channel(ch, processed, budget):
        called.append(ch["slug"])
        raise RuntimeError("HTTP Error 403: Forbidden")

    monkeypatch.setattr(pp, "process_channel", fake_process_channel)
    monkeypatch.setattr(pp, "_flag_auth_error_if_needed", lambda e: None)
    monkeypatch.setattr(sys, "argv", ["process_podcasts.py", "--budget", "5"])
    pp.main()
    # Unchanged behaviour: auth errors stop the loop (own alert step), and are
    # not channel errors.
    assert called == ["rav-first"]
    assert channel_errors.load() == []


# ---------------------------------------------------------------------------
# Workflow wiring
# ---------------------------------------------------------------------------

def _steps(text: str) -> list[tuple[str, str]]:
    """(name, body) of each step of the single job, in order."""
    parts = re.split(r"(?m)^      - name: ", text)
    return [(p.splitlines()[0].strip(), p) for p in parts[1:]]


def test_workflow_check_is_the_last_step_and_always_runs():
    steps = _steps(WORKFLOW.read_text(encoding="utf-8"))
    name, body = steps[-1]
    assert "channel_errors.py check" in body, name
    assert re.search(r"(?m)^        if: always\(\)\s*$", body), body


def test_workflow_nothing_between_process_and_push_is_conditional_on_success():
    steps = _steps(WORKFLOW.read_text(encoding="utf-8"))
    names = [n for n, _ in steps]
    i_process = next(i for i, n in enumerate(names) if n.startswith("Run podcast update"))
    i_push = next(i for i, n in enumerate(names) if n.startswith("Push the new state to R2"))
    assert i_process < i_push
    process_body = steps[i_process][1]
    assert "process_podcasts.py" in process_body
    # The process step itself must not swallow failures (a crash of the
    # script, not a channel error, must still stop before the push).
    assert "continue-on-error" not in process_body
    assert "|| true" not in process_body
    for name, body in steps[i_process + 1:i_push + 1]:
        assert not re.search(r"(?m)^        if:", body), f"step {name!r} has an if:"


def test_process_podcasts_no_longer_exits_1_on_channel_errors():
    src = (SCRIPTS / "process_podcasts.py").read_text(encoding="utf-8")
    main_src = src[src.index("def main():"):]
    assert "sys.exit(1)" not in main_src
    assert "channel_errors.record(errors" in main_src
