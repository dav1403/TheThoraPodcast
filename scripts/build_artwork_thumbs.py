"""
build_artwork_thumbs.py
-----------------------
Generates lightweight square thumbnails of the channel artworks.

Why: artwork/<slug>.png is the 3000x3000 Apple Podcasts master (0.5 to 5 MB
each, 52 MB for the whole roster). The site and the mobile app display those
same files inside 48-104 px circles/cards, so the home page alone used to pull
tens of megabytes to paint a handful of bubbles. GitHub Pages offers no CDN
resizing, so the small variant has to be committed alongside the master.

Output: artwork/thumb/<slug>.webp  (256x256, quality 82, ~15-25 KB)
        256 px covers every small display slot on the site (max 104 px) even on
        a 2x DPR screen. The full-size PNG is left untouched: it is what the RSS
        feeds advertise as <itunes:image> and Apple/Spotify require >= 1400 px.

The <slug> (case included) is preserved verbatim — several slugs are
intentionally capitalised and the site derives the thumbnail URL from the slug.

Idempotent: a state file (artwork/thumb/.state.json) records the size+mtime+
hash of each source, so an unchanged artwork is not re-encoded. This matters
because the script runs inside the hourly pipeline.

Run:
  python scripts/build_artwork_thumbs.py
  python scripts/build_artwork_thumbs.py --force     # re-encode everything
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

from PIL import Image

ARTWORK_DIR = Path("artwork")
THUMB_DIR = ARTWORK_DIR / "thumb"
STATE_FILE = THUMB_DIR / ".state.json"

THUMB_SIZE = 256
QUALITY = 82

# Suffixes accepted as a source artwork, in preference order.
SOURCE_SUFFIXES = (".png", ".jpg", ".jpeg")

# Not channel artwork: the social/OpenGraph banner is a wide image nobody
# displays in a bubble, and square-cropping it would be meaningless.
EXCLUDED_STEMS = {"og-banner"}


def source_files() -> list[Path]:
    """One master per slug, excluding the thumb/ output directory itself.

    A few slugs ship both a .png and a .jpg (e.g. Nahal-Haim): they would map to
    the same <slug>.webp, so SOURCE_SUFFIXES order decides — PNG wins, which is
    the file the site and the feeds actually reference.
    """
    by_stem: dict[str, Path] = {}
    for p in sorted(ARTWORK_DIR.iterdir()):
        suffix = p.suffix.lower()
        if p.is_dir() or suffix not in SOURCE_SUFFIXES or p.stem in EXCLUDED_STEMS:
            continue
        kept = by_stem.get(p.stem)
        if kept is None or SOURCE_SUFFIXES.index(suffix) < SOURCE_SUFFIXES.index(
            kept.suffix.lower()
        ):
            by_stem[p.stem] = p
    return [by_stem[s] for s in sorted(by_stem)]


def fingerprint(path: Path) -> str:
    """Content hash of the source — survives the mtime churn of a fresh CI clone."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return f"{path.stat().st_size}:{h.hexdigest()[:32]}"


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def make_thumb(src: Path, dest: Path) -> int:
    """Encode one 256x256 WebP, centre-cropped. Returns the output size in bytes."""
    with Image.open(src) as im:
        im = im.convert("RGB")
        w, h = im.size
        if w != h:  # centre-crop to a square before scaling
            side = min(w, h)
            left = (w - side) // 2
            top = (h - side) // 2
            im = im.crop((left, top, left + side, top + side))
        im = im.resize((THUMB_SIZE, THUMB_SIZE), Image.LANCZOS)
        dest.parent.mkdir(parents=True, exist_ok=True)
        im.save(dest, "WEBP", quality=QUALITY, method=6)
    return dest.stat().st_size


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="re-encode every thumbnail")
    args = ap.parse_args()

    if not ARTWORK_DIR.is_dir():
        print("artwork/ not found — nothing to do")
        return 0

    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    state = {} if args.force else load_state()
    new_state = {}

    built = skipped = failed = 0
    total_src = total_thumb = 0

    for src in source_files():
        slug = src.stem
        dest = THUMB_DIR / f"{slug}.webp"
        try:
            fp = fingerprint(src)
        except OSError as e:
            print(f"  [{slug}] unreadable source: {e}")
            failed += 1
            continue

        total_src += src.stat().st_size

        if not args.force and dest.exists() and state.get(src.name) == fp:
            new_state[src.name] = fp
            total_thumb += dest.stat().st_size
            skipped += 1
            continue

        try:
            size = make_thumb(src, dest)
        except Exception as e:  # a single corrupt artwork must not stop the pipeline
            print(f"  [{slug}] thumbnail failed: {e}")
            failed += 1
            continue

        new_state[src.name] = fp
        total_thumb += size
        built += 1
        print(f"  [{slug}] {src.stat().st_size // 1024} KB -> {size // 1024} KB")

    # Drop thumbnails whose source artwork disappeared (channel removed/renamed).
    live = {f"{p.stem}.webp" for p in source_files()}
    for stale in THUMB_DIR.glob("*.webp"):
        if stale.name not in live:
            stale.unlink()
            print(f"  removed stale thumbnail {stale.name}")

    STATE_FILE.write_text(
        json.dumps(new_state, indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )

    print(
        f"  artwork/thumb/  ({built} built, {skipped} unchanged, {failed} failed, "
        f"{total_thumb // 1024} KB total vs {total_src // 1024} KB of masters)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
