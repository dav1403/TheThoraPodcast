"""Pre-commit integrity checks: empty files, truncation, HTML structure, JS syntax."""
import subprocess, sys, json, re, os, tempfile

errors = []
NODE = r'C:\Program Files\nodejs\node.exe'


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


def check_js_in_html(path, content):
    if not os.path.isfile(NODE):
        return []
    text = content.decode("utf-8", errors="replace")
    # Extract only JS script blocks (skip src=, json, module imports that aren't JS)
    blocks = re.findall(
        r'<script(?!\s[^>]*\bsrc\b)(?!\s[^>]*type=["\']application/(?:ld\+)?json["\'])[^>]*>([\s\S]*?)</script>',
        text, re.IGNORECASE
    )
    js_errors = []
    for i, block in enumerate(blocks):
        if not block.strip() or len(block.strip()) < 10:
            continue
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp(suffix=".js")
            os.write(fd, block.encode("utf-8"))
            os.close(fd)
            r = subprocess.run(
                [NODE, "--check", tmp],
                capture_output=True, text=True, timeout=10
            )
            if r.returncode != 0:
                msg = r.stderr.strip()
                msg = re.sub(r'[^\n]+\.js:', '', msg).strip()
                first = msg.splitlines()[0] if msg else "syntax error"
                js_errors.append(
                    f"{path}: JS SYNTAX ERROR (bloc {i + 1}): {first}"
                )
        except Exception:
            pass
        finally:
            if tmp:
                try:
                    os.unlink(tmp)
                except Exception:
                    pass
    return js_errors


for path in staged_files():
    content = staged_bytes(path)
    size = len(content)
    lines = content.count(b"\n")

    if size == 0:
        errors.append(
            f"{path}: FICHIER VIDE (0 octets) - corrompu par sed/redirection"
        )
        continue

    head = head_bytes(path)
    if head is not None:
        head_lines = head.count(b"\n")
        if head_lines > 20 and lines < head_lines * 0.30:
            pct = 100 * lines // head_lines
            errors.append(
                f"{path}: TRONQUE - {lines} lignes vs {head_lines} dans HEAD ({pct}% restant)"
            )

    if path.endswith(".html"):
        snippet = content[:2000].lower()
        if b"<!doctype" not in snippet and b"<html" not in snippet:
            errors.append(f"{path}: HTML sans DOCTYPE ni html")
        errors.extend(check_js_in_html(path, content))

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
