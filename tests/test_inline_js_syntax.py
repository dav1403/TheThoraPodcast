"""Every inline <script> the generator emits must be syntactically valid JS.

This is the guard the project was missing. The `.githooks/pre-commit` JS check
only ever looked at *root-level* HTML, and it never runs in CI anyway — yet CI
is what commits the ~31 700 episode pages. So a generator bug that corrupted
every single episode page shipped to production unnoticed (f0105f42: a `\\n\\n`
separator written into a non-raw f-string became two real newlines, which broke
the JS string literal and therefore the whole main <script> of every episode
page — resume banner, share, favourite star, speed bar, transcript copy and the
language selector all died at once).

Rendering is what produces the bug, so rendering is where it has to be caught:
we run the generator over the tiny fixture dataset and hand every inline block
to `node --check`. Episode pages are covered by construction — no sampling
needed at this size — and the whole thing costs a couple of seconds.
"""
import glob
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

# Inline JS only: skip `src=` externals and JSON-LD / JSON payload blocks, which
# are not JavaScript and would fail `node --check` on their own.
_INLINE_JS_RE = re.compile(
    r'<script(?!\s[^>]*\bsrc\b)'
    r'(?!\s[^>]*type=["\']application/(?:ld\+)?json["\'])'
    r'[^>]*>([\s\S]*?)</script>',
    re.IGNORECASE,
)


def _node():
    """Path to the Node binary, or None when it is not installed.

    Missing Node must never turn this suite green by accident in CI, so the
    caller fails there instead of skipping.
    """
    return shutil.which("node")


def _syntax_errors(js: str):
    """Return node's first error line for `js`, or None when it parses."""
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(suffix=".js")
        os.write(fd, js.encode("utf-8"))
        os.close(fd)
        proc = subprocess.run(
            [_node(), "--check", tmp], capture_output=True, text=True, timeout=30
        )
        if proc.returncode == 0:
            return None
        msg = re.sub(r"[^\n]+\.js:", "", proc.stderr.strip()).strip()
        return msg.splitlines()[0] if msg else "syntax error"
    finally:
        if tmp:
            Path(tmp).unlink(missing_ok=True)


def test_every_generated_page_has_valid_inline_js(gen, fixture_workdir):
    node = _node()
    if node is None:
        if os.environ.get("CI"):
            pytest.fail("node is required in CI to validate generated inline JS")
        pytest.skip("node not installed locally")

    gen.main()

    pages = sorted(glob.glob("**/*.html", recursive=True))
    assert pages, "generator produced no HTML"

    # Guard the guard: if the fixtures ever stop producing episode pages, this
    # test would still pass while covering nothing.
    episode_pages = [p for p in pages if os.sep in p or "/" in p]
    assert episode_pages, "no episode (subdirectory) page generated to check"

    failures = []
    checked = 0
    for path in pages:
        text = Path(path).read_text(encoding="utf-8")
        for i, block in enumerate(_INLINE_JS_RE.findall(text)):
            if len(block.strip()) < 10:
                continue
            checked += 1
            err = _syntax_errors(block)
            if err:
                failures.append(f"{path} (bloc {i + 1}): {err}")

    assert checked, "no inline JS found in the generated pages"
    assert not failures, "invalid inline JS in generated pages:\n" + "\n".join(failures)


def test_transcript_copy_separator_is_escaped(gen, fixture_workdir):
    """Regression pin for f0105f42, independent of Node.

    The transcript-copy handler joins paragraphs on a JS `'\\n\\n'` literal. If
    the generator ever emits a real newline there again, the string literal is
    unterminated. Assert on the emitted bytes so this fails even where Node is
    unavailable.
    """
    gen.main()

    pages = [p for p in glob.glob("**/*.html", recursive=True) if "/" in p.replace(os.sep, "/")]
    assert pages, "no episode page generated"

    for path in pages:
        text = Path(path).read_text(encoding="utf-8")
        for match in re.finditer(r"\.join\((['\"])", text):
            quote = match.group(1)
            rest = text[match.end():]
            end = rest.find(quote)
            assert end != -1, f"unterminated join() literal in {path}"
            literal = rest[:end]
            assert "\n" not in literal, (
                f"real newline inside a JS string literal in {path}: "
                f".join({quote}{literal!r}...) — use a \\n escape in the generator"
            )
