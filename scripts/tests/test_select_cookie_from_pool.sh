#!/usr/bin/env bash
# Tests for scripts/select_cookie_from_pool.py — rotation + fallback logic.
# Run from the repo root:  bash scripts/tests/test_select_cookie_from_pool.sh
set -u

# Resolve a Python interpreter. Prefer the Windows `py` launcher (absent on CI
# Linux, so it falls through to python3 there) because on Windows a bare
# `python3` hits the Microsoft Store stub, not a real interpreter.
if command -v py >/dev/null 2>&1; then PY="py -3.11";
elif command -v python3 >/dev/null 2>&1; then PY="python3";
else PY="python"; fi

HERE="$(cd "$(dirname "$0")/../.." && pwd)"
SCRIPT="$HERE/scripts/select_cookie_from_pool.py"
OUT="$(mktemp)"; LOG="$(mktemp)"
trap 'rm -f "$OUT" "$LOG"' EXIT

# Fake pool of 3 Netscape-style cookies separated by the sentinel line.
POOL=$(cat <<'POOLEOF'
# Netscape HTTP Cookie File
.youtube.com	TRUE	/	TRUE	0	SID	AAA111
-----COOKIE-----
# Netscape HTTP Cookie File
.youtube.com	TRUE	/	TRUE	0	SID	BBB222
-----COOKIE-----
# Netscape HTTP Cookie File
.youtube.com	TRUE	/	TRUE	0	SID	CCC333
POOLEOF
)

pass=0; fail=0
check() { # desc expected_marker
  if grep -q "$2" "$OUT" 2>/dev/null; then echo "  ok: $1 -> $2"; pass=$((pass+1));
  else echo "  FAIL: $1 (expected $2)"; fail=$((fail+1)); fi
}

echo "== rotation across run numbers (pool of 3) =="
for pair in "0 AAA111" "1 BBB222" "2 CCC333" "3 AAA111" "4 BBB222"; do
  set -- $pair
  : > "$OUT"
  YOUTUBE_COOKIES_POOL="$POOL" COOKIE_ROTATION_INDEX="$1" COOKIE_ROTATION_OFFSET=0 \
    $PY "$SCRIPT" --output "$OUT" >/dev/null
  check "run=$1" "$2"
done

echo "== offset lane picks a different cookie at same slot =="
: > "$OUT"
YOUTUBE_COOKIES_POOL="$POOL" COOKIE_ROTATION_INDEX=0 COOKIE_ROTATION_OFFSET=1 \
  $PY "$SCRIPT" --output "$OUT" >/dev/null
check "offset=1 at run=0" "BBB222"

echo "== fallback: no pool, single secret =="
: > "$OUT"
YOUTUBE_COOKIES="SINGLE_COOKIE_XYZ" $PY "$SCRIPT" --output "$OUT" >/dev/null
check "single fallback" "SINGLE_COOKIE_XYZ"

echo "== fallback: malformed pool (only separators/blank) -> single =="
: > "$OUT"
YOUTUBE_COOKIES_POOL="$(printf -- '-----COOKIE-----\n\n-----COOKIE-----\n')" \
  YOUTUBE_COOKIES="SINGLE_FALLBACK" $PY "$SCRIPT" --output "$OUT" >"$LOG"
check "malformed pool falls back" "SINGLE_FALLBACK"
if grep -q "::warning::" "$LOG"; then echo "  ok: warned on malformed pool"; pass=$((pass+1));
else echo "  FAIL: no warning on malformed pool"; fail=$((fail+1)); fi

echo "== no cookies at all -> no file written, exit 0 =="
rm -f "$OUT"
env -u YOUTUBE_COOKIES -u YOUTUBE_COOKIES_POOL $PY "$SCRIPT" --output "$OUT" >/dev/null
if [ ! -s "$OUT" ]; then echo "  ok: no file written"; pass=$((pass+1));
else echo "  FAIL: file written unexpectedly"; fail=$((fail+1)); fi

echo ""
echo "RESULT: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
