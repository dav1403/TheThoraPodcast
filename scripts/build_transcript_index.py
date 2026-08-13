#!/usr/bin/env python3
"""
build_transcript_index.py
-------------------------
Builds a SHARDED inverted index over feeds/transcripts/<video_id>.txt so the
site can offer full-text search *inside* the classes (not just titles).

Why sharded: the corpus is ~500 MB of raw text over ~16 000 non-empty
transcripts. Nothing of that size can ever reach the browser. The index is
therefore pre-computed in CI and split so a query only downloads the few small
JSON files it actually needs:

    search-fts/
      manifest.json        config + build stamp (the only file loaded eagerly)
      terms/t_<prefix>.json  {term: [docId, tf, docId, tf, ...]} — one file per
                           PREFIX_LEN-character normalised prefix of the term.
                           The "t_" prefix keeps names like "aux" from colliding
                           with Windows reserved device names on a dev machine.
      docs/<n>.json        [[video_id, title, channel, url, date], ...] — doc
                           metadata for ids [n*DOC_SHARD, (n+1)*DOC_SHARD)
      .build-state.json    corpus fingerprint + timestamp (skip identical rebuild)

The front-end (index.html) tokenises the query the same way, fetches one term
shard per query term, intersects the posting lists, then fetches only the doc
shards holding the winning ids. Snippets are NOT stored in the index: the client
fetches the handful of transcript .txt files it needs to display an excerpt.

Size control (the index is committed to git every build, so it must stay small):
  * stopwords FR/EN/HE + minimum token length,
  * a term is dropped when it occurs only once in the whole corpus (ASR noise),
  * a term is dropped when it appears in more than MAX_DF_RATIO of documents,
  * a posting list is capped to MAX_POSTINGS documents, keeping the highest tf.

Usage:
    python scripts/build_transcript_index.py                # build if corpus changed
    python scripts/build_transcript_index.py --force        # always rebuild
    python scripts/build_transcript_index.py --min-interval-hours 6
"""
import argparse
import hashlib
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate_channel_pages import ep_path  # noqa: E402  (URL form must match the site)

FEEDS_DIR = Path("feeds")
TRANSCRIPTS_DIR = FEEDS_DIR / "transcripts"
CHANNELS_FILE = Path("channels.json")
OUT_DIR = Path("search-fts")
STATE_FILE = OUT_DIR / ".build-state.json"

INDEX_VERSION = 1          # bump when the on-disk format changes
PREFIX_LEN = 3             # term shard key = first PREFIX_LEN normalised chars
DOC_SHARD = 1000           # documents per docs/<n>.json shard
MIN_LEN_LATIN = 3          # shorter latin tokens are noise ("le", "un", "ça")
MIN_LEN_HEBREW = 2         # hebrew words are denser; 2 letters can be a word
MAX_TOKENS_PER_DOC = 40000 # ~4 h of speech; guards against outlier transcripts
MAX_POSTINGS = 30          # documents kept per term (highest tf first)
MAX_DF_RATIO = 0.30        # a term in >30 % of docs carries no signal
MIN_TOTAL_TF = 3           # drop near-hapax terms (overwhelmingly ASR garbage)
TITLE_BOOST = 4            # tf bonus for a term present in the episode title
TF_CAP = 255               # postings stay one byte wide in spirit

# yt-dlp VTT headers leak into the cleaned text; never index them.
HEADER_RE = re.compile(r"^\s*Kind:\s*captions\s+Language:\s*\S+\s*", re.IGNORECASE)

# Latin runs and Hebrew runs, after normalisation (accents and niqqud removed).
TOKEN_RE = re.compile(r"[a-z]+|[א-ת]+")

# Accent folding + hebrew mark removal via str.translate. A full
# unicodedata.normalize("NFD", …) pass over the ~500 MB corpus costs ~80 s of CI
# time; a translation table does the same job for the characters that actually
# occur in French/Hebrew captions at a fraction of the cost. The client-side
# tokeniser in index.html mirrors this behaviour with String.normalize('NFD').
_FOLD_PAIRS = {
    "àáâãäå": "a", "ç": "c", "èéêë": "e", "ìíîï": "i", "ñ": "n",
    "òóôõöø": "o", "ùúûü": "u", "ýÿ": "y", "œ": "oe", "æ": "ae",
}
FOLD_MAP: dict[int, str] = {}
for _src, _dst in _FOLD_PAIRS.items():
    for _c in _src:
        FOLD_MAP[ord(_c)] = _dst
# Niqqud, cantillation marks and hebrew punctuation (geresh/gershayim) → space.
for _cp in list(range(0x0591, 0x05C8)) + [0x05F3, 0x05F4]:
    FOLD_MAP[_cp] = " "

STOPWORDS = set("""
alors aussi autre autres avec avoir bien cela celui cent ces cest cet cette
chaque chose comme comment dans des deux dire donc dont elle elles encore entre
etait etaient etant etc etre eux fait faire fois font hein ici ils jamais juste
leur leurs lui maintenant mais meme memes mes moins mon ntre nos notre nous
oui par parce pas peu peut plus por posr pour pourquoi quand que quel quelle
quelque quelques qui quoi sans ses sest sera seront ses seulement soit son sont
sous sur tous tout toute toutes tres trop une uns vais vers veut voila vont
vous etais etes avez avais avait avons aviez ont oon ete
the and for that with this from you your are was were have has had not but
they them their there then than what which when where who will would can could
about into more some such only other over also its his her because been being
one two out all any how our per
של את זה זאת הוא היא הם הן אני אנחנו אתה אתם היה היו יש אין כל כמו אבל אשר
אז גם רק עוד כאן שם מה מי איך למה כי אם או על אל לא כן עם בין אחרי לפני
""".split())


def normalise(text: str) -> str:
    """Lowercase, fold latin accents, drop hebrew niqqud."""
    return text.lower().translate(FOLD_MAP)


def keep_token(tok: str) -> bool:
    min_len = MIN_LEN_HEBREW if tok[0] >= "א" else MIN_LEN_LATIN
    return len(tok) >= min_len and tok not in STOPWORDS


def count_tokens(text: str, limit: int = MAX_TOKENS_PER_DOC) -> dict[str, int]:
    """term -> raw frequency. Counting happens in C (Counter over the regex
    matches) and the per-token filtering then runs over the ~2 000 *distinct*
    terms of a transcript instead of its ~5 000+ tokens."""
    tokens = TOKEN_RE.findall(normalise(text))
    if len(tokens) > limit:
        tokens = tokens[:limit]
    return {tok: n for tok, n in Counter(tokens).items() if keep_token(tok)}


def shard_key(term: str) -> str:
    """Shard name for a term: its first PREFIX_LEN chars, hex-escaped for
    non-ASCII so the filename is safe on every filesystem (hebrew prefixes
    become e.g. 'x05d0x05d1'). The caller prefixes the file name with "t_"."""
    head = term[:PREFIX_LEN]
    if head.isascii():
        return head
    return "".join(c if c.isascii() else f"x{ord(c):04x}" for c in head)


def load_episodes() -> dict[str, dict]:
    """video_id -> {t, c, u, d}. Mirrors build_search_index(): an episode
    without title or published date has no generated page, so it is skipped."""
    channels = json.loads(CHANNELS_FILE.read_text(encoding="utf-8-sig"))
    episodes: dict[str, dict] = {}
    for ch in channels:
        if not ch.get("enabled"):
            continue
        entries_path = FEEDS_DIR / f"{ch['slug']}.entries.json"
        if not entries_path.exists():
            continue
        entries = json.loads(entries_path.read_text(encoding="utf-8"))
        for ep in entries:
            vid = ep.get("video_id")
            title = ep.get("title") or ""
            published = ep.get("published") or ""
            if not vid or not title or not published or vid in episodes:
                continue
            episodes[vid] = {
                "t": title,
                "c": ch["podcast_author"],
                "u": ep_path(ch["slug"], ep),
                "d": published[:10],
            }
    return episodes


def corpus_fingerprint(files: list[Path]) -> str:
    """Cheap change detector: names + sizes. CI checkouts reset mtimes, and
    hashing 500 MB every hour would cost more than the rebuild it saves."""
    h = hashlib.sha1()
    for p in sorted(files, key=lambda x: x.name):
        h.update(p.name.encode())
        h.update(b":")
        h.update(str(p.stat().st_size).encode())
        h.update(b"\n")
    return h.hexdigest()


def should_build(fingerprint: str, args) -> tuple[bool, str]:
    if args.force:
        return True, "forced"
    if not STATE_FILE.exists():
        return True, "no previous build"
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return True, "unreadable state file"
    if state.get("index_version") != INDEX_VERSION:
        return True, "index format changed"
    if state.get("fingerprint") != fingerprint:
        if args.min_interval_hours > 0 and state.get("built_at"):
            try:
                last = datetime.fromisoformat(state["built_at"])
                age_h = (datetime.now(timezone.utc) - last).total_seconds() / 3600
            except Exception:
                age_h = 1e9
            if age_h < args.min_interval_hours:
                return False, (f"corpus changed but last build was {age_h:.1f} h ago "
                               f"(< --min-interval-hours {args.min_interval_hours})")
        return True, "corpus changed"
    return False, "corpus unchanged"


def write_json(path: Path, data) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n"
    path.write_text(payload, encoding="utf-8", newline="\n")
    return len(payload.encode("utf-8"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true",
                        help="rebuild even if the corpus is unchanged")
    parser.add_argument("--min-interval-hours", type=float, default=0.0,
                        help="skip a rebuild if the previous one is younger than this "
                             "(caps how often ~10 MB of shards churn in git)")
    args = parser.parse_args()

    t0 = time.time()
    if not TRANSCRIPTS_DIR.exists():
        print("No feeds/transcripts/ directory — nothing to index.")
        return

    files = [p for p in TRANSCRIPTS_DIR.glob("*.txt") if p.stat().st_size > 0]
    print(f"=== Build transcript index — {len(files)} non-empty transcripts ===")

    fingerprint = corpus_fingerprint(files)
    build, reason = should_build(fingerprint, args)
    if not build:
        print(f"Skipping rebuild: {reason}.")
        return
    print(f"Rebuilding: {reason}.")

    episodes = load_episodes()
    print(f"  {len(episodes)} episodes with a generated page")

    prev_doc_ids: dict[str, int] = {}
    if STATE_FILE.exists():
        try:
            prev = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if prev.get("index_version") == INDEX_VERSION:
                prev_doc_ids = {k: int(v) for k, v in (prev.get("doc_ids") or {}).items()}
        except Exception as e:
            print(f"  WARN: previous state unusable ({e}) — doc ids will be reassigned")

    # ── Pass 1: tokenise ──────────────────────────────────────────────────────
    # Doc ids are ASSIGNED ONCE per video_id and carried over from the previous
    # build (append-only, persisted in .build-state.json). This is what keeps the
    # index git-friendly: with positional ids, adding a single transcript would
    # renumber every later document and therefore rewrite all ~17 000 term
    # shards each build. With stable ids, only the shards holding the terms of
    # the newly indexed transcripts actually change content, and git records a
    # diff for those alone.
    doc_ids: dict[str, int] = dict(prev_doc_ids)
    next_id = max(doc_ids.values(), default=-1) + 1
    entries_by_id: dict[int, list] = {}
    postings: dict[str, list[tuple[int, int]]] = {}
    total_tf: dict[str, int] = {}
    indexed = orphans = 0

    for path in sorted(files, key=lambda p: p.name):
        vid = path.stem
        meta = episodes.get(vid)
        if meta is None:
            orphans += 1
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            print(f"  WARN: cannot read {path.name}: {e}")
            continue
        text = HEADER_RE.sub("", text, count=1)

        tf = count_tokens(text)
        if not tf:
            continue
        for tok in count_tokens(meta["t"], limit=200):
            tf[tok] = tf.get(tok, 0) + TITLE_BOOST

        doc_id = doc_ids.get(vid)
        if doc_id is None:
            doc_id = next_id
            doc_ids[vid] = doc_id
            next_id += 1
        entries_by_id[doc_id] = [vid, meta["t"], meta["c"], meta["u"], meta["d"]]
        for tok, n in tf.items():
            n = min(n, TF_CAP)
            postings.setdefault(tok, []).append((doc_id, n))
            total_tf[tok] = total_tf.get(tok, 0) + n
        indexed += 1
        if indexed % 2000 == 0:
            print(f"  … {indexed} transcripts tokenised ({time.time() - t0:.0f}s)")

    # Retired ids (transcript deleted, episode unpublished) leave a null hole so
    # every surviving id keeps pointing at the same document.
    docs: list[list | None] = [entries_by_id.get(i) for i in range(next_id)]
    holes = next_id - indexed
    print(f"  {indexed} indexed, {orphans} transcripts without a matching episode page"
          + (f", {holes} retired id(s)" if holes else ""))
    print(f"  {len(postings):,} raw terms  ({time.time() - t0:.0f}s)")

    # ── Pass 2: prune + shard ─────────────────────────────────────────────────
    max_df = max(2, int(indexed * MAX_DF_RATIO))
    shards: dict[str, dict[str, list[int]]] = {}
    kept = dropped_rare = dropped_common = 0
    for term, plist in postings.items():
        if total_tf[term] < MIN_TOTAL_TF:
            dropped_rare += 1
            continue
        if len(plist) > max_df:
            dropped_common += 1
            continue
        if len(plist) > MAX_POSTINGS:
            plist = sorted(plist, key=lambda x: -x[1])[:MAX_POSTINGS]
        flat: list[int] = []
        for doc_id, n in sorted(plist):
            flat.append(doc_id)
            flat.append(n)
        shards.setdefault(shard_key(term), {})[term] = flat
        kept += 1
    print(f"  {kept:,} terms kept, {dropped_rare:,} hapax dropped, "
          f"{dropped_common:,} over-common dropped (df > {max_df})")

    # ── Write ─────────────────────────────────────────────────────────────────
    # Remove stale shards from a previous build before writing the new ones,
    # otherwise a term prefix that disappeared would keep serving dead postings.
    for old in (OUT_DIR / "terms").glob("t_*.json"):
        old.unlink()
    for old in (OUT_DIR / "docs").glob("*.json"):
        old.unlink()

    term_bytes = 0
    biggest = []
    for key, mapping in shards.items():
        size = write_json(OUT_DIR / "terms" / f"t_{key}.json", mapping)
        term_bytes += size
        biggest.append((size, key, len(mapping)))
    biggest.sort(reverse=True)

    doc_bytes = 0
    n_doc_shards = (len(docs) + DOC_SHARD - 1) // DOC_SHARD
    for i in range(n_doc_shards):
        doc_bytes += write_json(OUT_DIR / "docs" / f"{i}.json",
                                docs[i * DOC_SHARD:(i + 1) * DOC_SHARD])

    built_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    write_json(OUT_DIR / "manifest.json", {
        "index_version": INDEX_VERSION,
        "built_at": built_at,
        "prefix_len": PREFIX_LEN,
        "doc_shard_size": DOC_SHARD,
        "min_len_latin": MIN_LEN_LATIN,
        "min_len_hebrew": MIN_LEN_HEBREW,
        "max_postings": MAX_POSTINGS,
        "title_boost": TITLE_BOOST,
        "n_docs": indexed,
        "max_doc_id": next_id - 1,
        "n_terms": kept,
        "n_term_shards": len(shards),
        "n_doc_shards": n_doc_shards,
        "stopwords": sorted(STOPWORDS),
        "transcript_path": "feeds/transcripts/",
        "term_shard_pattern": "search-fts/terms/t_<prefix>.json",
    })
    write_json(STATE_FILE, {
        "index_version": INDEX_VERSION,
        "fingerprint": fingerprint,
        "built_at": built_at,
        "n_docs": indexed,
        "n_files": len(files),
        # video_id -> doc id, append-only. Dropping this file is safe but forces
        # a renumbering, i.e. one build where every term shard changes in git.
        "doc_ids": dict(sorted(doc_ids.items())),
    })

    print(f"  search-fts/terms/  {len(shards)} shards, {term_bytes / 1e6:.1f} MB")
    print(f"  search-fts/docs/   {n_doc_shards} shards, {doc_bytes / 1e6:.1f} MB")
    print("  largest term shards: " + ", ".join(
        f"{k}={s / 1024:.0f}KB/{n}t" for s, k, n in biggest[:5]))
    print(f"=== Done in {time.time() - t0:.0f}s ===")


if __name__ == "__main__":
    main()
