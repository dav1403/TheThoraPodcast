"""Pre-commit integrity checks: empty files, truncation, HTML structure, JS syntax,
duplicate function declarations, and missing required functions."""
import subprocess, sys, json, re, os, tempfile

errors = []
NODE = r'C:\Program Files\nodejs\node.exe'

# Functions defined in utils.js that must NOT be redeclared in HTML pages
UTILS_FUNCTIONS = {
    'escapeHtml', 'slugify', 'epUrl', 'formatDate', 'formatDuration',
    'playIcon', 'pauseIcon', 'shareIcon', 'filterDurAll', 'setDur',
}

# Required functions per page (must exist in inline JS)
REQUIRED_FUNCTIONS = {
    'paracha.html':      {'matchesParacha', 'findEpisodesForParacha', 'extractSlugFromHebcal'},
    'derniers-cours.html': {'filterDurDC', 'filteredFlat', 'renderList'},
    'themes.html':       {'loadAll', 'renderGrid', 'renderEpisodeList', 'openTheme'},
    'daf-hayomi.html':   {'renderList', 'renderItem'},
}


def run(cmd):
    return subprocess.run(cmd, capture_output=True)


def staged_files():
    r = run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"])
    return [f for f in r.stdout.decode(errors="replace").splitlines()
            if f.endswith((".html", ".json", ".py"))]


def head_bytes(path):
    r = run(["git", "show", f"HEAD:{path}"])
    return r.stdout if r.returncode == 0 else None


def staged_bytes(path):
    return run(["git", "show", f":{path}"]).stdout


def extract_inline_js(text):
    """Extract all inline JS blocks (not src=, not JSON)."""
    return re.findall(
        r'<script(?!\s[^>]*\bsrc\b)(?!\s[^>]*type=["\']application/(?:ld\+)?json["\'])[^>]*>([\s\S]*?)</script>',
        text, re.IGNORECASE
    )


def is_episode_page(path):
    return path.endswith(".html") and "/" in path.replace("\\", "/")


def sample_episode_pages(paths):
    """Pick a cheap but representative subset of staged episode pages.

    A regeneration stages ~31 700 episode pages; `node --check` on all of them
    would take many minutes and make the hook unusable. They all come out of the
    same template, so a generator bug hits every one of them at once — checking
    the first page of each channel directory (alphabetically, so the pick is
    deterministic) catches it while keeping the hook to a couple of seconds.
    """
    by_channel = {}
    for p in sorted(paths):
        channel = p.replace("\\", "/").split("/")[0]
        by_channel.setdefault(channel, p)
    return set(by_channel.values())


def check_js_syntax(path, content):
    if not os.path.isfile(NODE):
        return []
    text = content.decode("utf-8", errors="replace")
    blocks = extract_inline_js(text)
    js_errors = []
    for i, block in enumerate(blocks):
        if not block.strip() or len(block.strip()) < 10:
            continue
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp(suffix=".js")
            os.write(fd, block.encode("utf-8"))
            os.close(fd)
            r = subprocess.run([NODE, "--check", tmp], capture_output=True, text=True, timeout=10)
            if r.returncode != 0:
                msg = r.stderr.strip()
                msg = re.sub(r'[^\n]+\.js:', '', msg).strip()
                first = msg.splitlines()[0] if msg else "syntax error"
                js_errors.append(f"{path}: JS SYNTAX ERROR (bloc {i + 1}): {first}")
        except Exception:
            pass
        finally:
            if tmp:
                try: os.unlink(tmp)
                except Exception: pass
    return js_errors


def check_duplicate_functions(path, content):
    """Detect functions from utils.js redeclared in HTML pages."""
    if not path.endswith(".html"):
        return []
    text = content.decode("utf-8", errors="replace")
    blocks = extract_inline_js(text)
    combined_js = "\n".join(blocks)
    dupes = []
    for fn in UTILS_FUNCTIONS:
        # function declaration (not call)
        if re.search(rf'\bfunction\s+{re.escape(fn)}\s*\(', combined_js):
            dupes.append(f"{path}: FONCTION DUPLIQUEE '{fn}' - deja definie dans utils.js")
    return dupes


def check_required_functions(path, content):
    """Verify that required functions exist in the page."""
    fname = os.path.basename(path)
    required = REQUIRED_FUNCTIONS.get(fname)
    if not required:
        return []
    text = content.decode("utf-8", errors="replace")
    blocks = extract_inline_js(text)
    combined_js = "\n".join(blocks)
    missing = []
    for fn in required:
        if not re.search(rf'\bfunction\s+{re.escape(fn)}\s*\(', combined_js):
            missing.append(f"{path}: FONCTION REQUISE MANQUANTE '{fn}'")
    return missing


def check_orphaned_code(path, content):
    """Detect common orphaned code patterns left after edits."""
    if not path.endswith(".html"):
        return []
    text = content.decode("utf-8", errors="replace")
    blocks = extract_inline_js(text)
    combined_js = "\n".join(blocks)
    issues = []
    # _activeDur used but not declared (only in inline JS, not utils.js)
    if '_activeDur' in combined_js:
        if not re.search(r'\b(let|var|const)\s+_activeDur\b', combined_js):
            issues.append(f"{path}: '_activeDur' utilise mais non declare (doublon utils.js supprime?)")
    return issues


# ── Main check loop ───────────────────────────────────────────────────────────
STAGED = staged_files()
# Episode pages are generated in bulk, so only a sample gets the (slow) Node
# syntax check; root-level pages are hand-edited and are all checked.
JS_SAMPLE = sample_episode_pages([p for p in STAGED if is_episode_page(p)])

for path in STAGED:
    content = staged_bytes(path)
    size = len(content)
    lines = content.count(b"\n")

    if size == 0:
        errors.append(f"{path}: FICHIER VIDE (0 octets) - corrompu par sed/redirection")
        continue

    head = head_bytes(path)
    if head is not None:
        head_lines = head.count(b"\n")
        is_redirect = b'http-equiv="refresh"' in content
        if head_lines > 20 and lines < head_lines * 0.30 and not is_redirect:
            pct = 100 * lines // head_lines
            errors.append(f"{path}: TRONQUE - {lines} lignes vs {head_lines} dans HEAD ({pct}% restant)")

    if path.endswith(".html"):
        snippet = content[:2000].lower()
        if b"<!doctype" not in snippet and b"<html" not in snippet:
            errors.append(f"{path}: HTML sans DOCTYPE ni html")
        if not is_episode_page(path) or path in JS_SAMPLE:
            errors.extend(check_js_syntax(path, content))
        errors.extend(check_duplicate_functions(path, content))
        errors.extend(check_required_functions(path, content))
        errors.extend(check_orphaned_code(path, content))

    if path.endswith(".json"):
        try:
            json.loads(content.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as e:
            errors.append(f"{path}: JSON INVALIDE - {e}")

if errors:
    print("\n\033[31m[pre-commit] Commit bloque - problemes detectes:\033[0m\n")
    for e in errors:
        print(f"  \033[31mX\033[0m {e}")
    print("\nCorrigez, re-stagez et recommittez.\n")
    sys.exit(1)

sys.exit(0)
