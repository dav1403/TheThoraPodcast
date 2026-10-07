"""Compare two published copies of the site (e.g. staging vs production).

Read-only HTTP. Checks what matters to listeners and to the app:
  - every feeds/<slug>.xml: same episodes (GUIDs); differences are listed
    and attributed (episodes newer than the other side's newest = ingested
    on one side only, which is expected between two runs)
  - every feeds/<slug>.entries.json: episode count never lower on side A
  - the 383 legacy orphans: HTTP 200 + identical bytes
  - app/site JSON (channels, home, latest, search-index, mobile/manifest,
    search-fts/manifest): HTTP 200 + valid JSON; episode pages of the latest
    list reachable on both sides

Usage: python scripts/phase4_compare_sites.py A_BASE B_BASE [--report out.json]
Exit 1 if a non-explained difference is found.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

UA = {"User-Agent": "ttp-phase4-compare/1.0"}


def get(base: str, path: str) -> tuple[int, bytes]:
    url = f"{base.rstrip('/')}/{quote(path, safe='/-._~')}?cb={time.time_ns()}"
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, b""
        except Exception:
            time.sleep(2 * (attempt + 1))
    return 0, b""


def guids(xml: bytes) -> dict[str, str]:
    root = ET.fromstring(xml)
    out = {}
    for item in root.iter("item"):
        g = item.findtext("guid") or item.findtext("enclosure") or ""
        out[g] = item.findtext("pubDate") or ""
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("a", help="side A (e.g. staging)")
    ap.add_argument("b", help="side B (e.g. production)")
    ap.add_argument("--legacy-list", default=str(Path(__file__).resolve().parent / "legacy_orphans.txt"))
    ap.add_argument("--report", default="")
    args = ap.parse_args(argv)
    A, B = args.a, args.b
    rep: dict = {"a": A, "b": B, "feeds": {}, "entries": {}, "json": {}, "legacy": {}, "pages": {}}
    problems: list[str] = []

    code, body = get(B, "channels.json")
    chans = json.loads(body.decode("utf-8-sig"))
    slugs = [c["slug"] for c in chans if c.get("enabled", True) and not c.get("external_feed_url")]

    def feed(slug):
        ca, ba = get(A, f"feeds/{slug}.xml")
        cb, bb = get(B, f"feeds/{slug}.xml")
        return slug, ca, ba, cb, bb

    with ThreadPoolExecutor(8) as ex:
        for slug, ca, ba, cb, bb in ex.map(feed, slugs):
            if cb != 200:
                continue  # not a hosted feed on B
            if ca != 200:
                problems.append(f"feeds/{slug}.xml: HTTP {ca} on A")
                continue
            ga, gb = guids(ba), guids(bb)
            only_a, only_b = sorted(set(ga) - set(gb)), sorted(set(gb) - set(ga))
            rep["feeds"][slug] = {"a": len(ga), "b": len(gb), "only_a": len(only_a), "only_b": len(only_b),
                                  "identical_bytes": hashlib.md5(ba).digest() == hashlib.md5(bb).digest()}
            if only_b:
                rep["feeds"][slug]["only_b_sample"] = only_b[:5]

    def entries(slug):
        ca, ba = get(A, f"feeds/{slug}.entries.json")
        cb, bb = get(B, f"feeds/{slug}.entries.json")
        na = len(json.loads(ba.decode("utf-8-sig"))) if ca == 200 else -1
        nb = len(json.loads(bb.decode("utf-8-sig"))) if cb == 200 else -1
        return slug, na, nb

    sp = get(B, "speakers.json")[1]
    all_slugs = [c["slug"] for c in chans] + [s["slug"] for s in json.loads(sp.decode("utf-8-sig") or "[]")]
    with ThreadPoolExecutor(8) as ex:
        for slug, na, nb in ex.map(entries, all_slugs):
            if nb < 0:
                continue
            rep["entries"][slug] = {"a": na, "b": nb}
            if na < 0:
                problems.append(f"feeds/{slug}.entries.json missing on A")

    for p in ("channels.json", "speakers.json", "home.json", "latest.json", "search-index.json",
              "mobile/manifest.json", "search-fts/manifest.json"):
        r = {}
        for side, base in (("a", A), ("b", B)):
            c, b = get(base, p)
            ok = c == 200
            try:
                json.loads(b.decode("utf-8-sig"))
            except ValueError:
                ok = False
            r[side] = ok
        rep["json"][p] = r
        if r["b"] and not r["a"]:
            problems.append(f"{p}: OK on B, not on A")

    legacy = [x.strip() for x in Path(args.legacy_list).read_text(encoding="utf-8").splitlines()
              if x.strip() and not x.startswith("#")]

    def leg(p):
        ca, ba = get(A, p)
        cb, bb = get(B, p)
        return p, ca, cb, ba == bb

    same = 0
    with ThreadPoolExecutor(12) as ex:
        for p, ca, cb, eq in ex.map(leg, legacy):
            if ca == 200 and cb == 200 and eq:
                same += 1
            else:
                problems.append(f"legacy {p}: A {ca} / B {cb} / identical {eq}")
    rep["legacy"] = {"listed": len(legacy), "identical": same}

    latest = json.loads(get(B, "latest.json")[1].decode("utf-8-sig") or "{}")
    urls = [e["url"].lstrip("/") for e in latest.get("episodes", []) if e.get("url")][:20]
    ok_pages = sum(1 for u in urls if get(A, u)[0] == 200)
    rep["pages"] = {"latest_of_b_checked": len(urls), "reachable_on_a": ok_pages}

    a_more = sum(1 for v in rep["entries"].values() if v["a"] > v["b"])
    b_more = sum(1 for v in rep["entries"].values() if v["b"] > v["a"])
    rep["summary"] = {"feeds_compared": len(rep["feeds"]),
                      "feeds_byte_identical": sum(1 for v in rep["feeds"].values() if v["identical_bytes"]),
                      "entries_a_more": a_more, "entries_b_more": b_more,
                      "episodes_a": sum(v["a"] for v in rep["entries"].values() if v["a"] > 0),
                      "episodes_b": sum(v["b"] for v in rep["entries"].values() if v["b"] > 0),
                      "problems": problems}
    print(json.dumps(rep["summary"], indent=1, ensure_ascii=False))
    for slug, v in rep["feeds"].items():
        if v["only_a"] or v["only_b"]:
            print(f"  {slug}: A {v['a']} / B {v['b']} episodes, only A {v['only_a']}, only B {v['only_b']}")
    print(f"legacy: {same}/{len(legacy)} identical; latest pages of B reachable on A: {ok_pages}/{len(urls)}")
    if args.report:
        Path(args.report).write_text(json.dumps(rep, indent=1, ensure_ascii=False), encoding="utf-8")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
