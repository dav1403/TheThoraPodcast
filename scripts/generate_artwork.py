"""
generate_artwork.py
-------------------
Generates 3000x3000 PNG podcast artwork for each enabled channel.
- Fetches the best available YouTube channel thumbnail
- Upscales to 3000x3000 with LANCZOS resampling
- Adds a gradient bar + channel name text at the bottom
- Saves to artwork/<slug>.png (RGB, no alpha, 72 dpi)
- Skips channels whose artwork already exists

Apple Podcasts requirements met:
  - 3000x3000 pixels
  - PNG format, .png extension
  - RGB colorspace, no alpha channel
  - 72 dpi
  - Hosted on GitHub Pages (allows HTTP HEAD + Last-Modified)

Run manually:
  python scripts/generate_artwork.py

Or with --force to regenerate all artwork:
  python scripts/generate_artwork.py --force
"""

import argparse
import io
import json
import os
import sys
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

CHANNELS_FILE = "channels.json"
ARTWORK_DIR   = Path("artwork")
SIZE          = 3000   # Apple max (and minimum is 1400)
DPI           = 72

# Gradient bar height as a fraction of the total image height
BAR_FRACTION  = 0.22


def fetch_best_thumbnail(channel_id: str, api_key: str) -> bytes:
    """Fetch the best available channel thumbnail from YouTube Data API."""
    url = (
        f"https://www.googleapis.com/youtube/v3/channels"
        f"?key={api_key}&id={channel_id}&part=snippet"
    )
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    data = r.json()

    if "error" in data:
        raise Exception(f"YouTube API error: {data['error']['message']}")

    items = data.get("items", [])
    if not items:
        raise Exception(f"No channel found for ID: {channel_id}")

    thumbnails = items[0]["snippet"].get("thumbnails", {})
    thumb = (
        thumbnails.get("maxres") or
        thumbnails.get("high")   or
        thumbnails.get("medium") or
        thumbnails.get("default") or
        {}
    )
    thumb_url = thumb.get("url", "")
    if not thumb_url:
        raise Exception(f"No thumbnail URL found for channel {channel_id}")

    print(f"  Downloading thumbnail: {thumb_url}")
    r2 = requests.get(thumb_url, timeout=30)
    r2.raise_for_status()
    return r2.content


def find_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Try common bold font paths; fall back to PIL default."""
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "C:/Windows/Fonts/arialbd.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def generate_artwork(channel_name: str, thumb_bytes: bytes, out_path: Path):
    """Create a 3000x3000 PNG from a thumbnail + channel name overlay."""
    # --- open and fill-fit the thumbnail to 3000x3000 ---
    img = Image.open(io.BytesIO(thumb_bytes)).convert("RGB")
    src_w, src_h = img.size

    # Scale so the image covers the full canvas (crop if needed)
    scale = max(SIZE / src_w, SIZE / src_h)
    new_w = int(src_w * scale)
    new_h = int(src_h * scale)
    img = img.resize((new_w, new_h), Image.LANCZOS)

    # Centre-crop to SIZE x SIZE
    left = (new_w - SIZE) // 2
    top  = (new_h - SIZE) // 2
    img  = img.crop((left, top, left + SIZE, top + SIZE))

    draw = ImageDraw.Draw(img)

    # --- gradient bar at the bottom ---
    bar_h    = int(SIZE * BAR_FRACTION)
    bar_top  = SIZE - bar_h

    # Build a vertical gradient from transparent → solid dark
    gradient = Image.new("RGBA", (SIZE, bar_h), (0, 0, 0, 0))
    grad_draw = ImageDraw.Draw(gradient)
    for y in range(bar_h):
        alpha = int(220 * (y / bar_h))   # 0 → 220
        grad_draw.line([(0, y), (SIZE, y)], fill=(10, 10, 10, alpha))

    # Composite gradient onto image
    img_rgba = img.convert("RGBA")
    img_rgba.paste(gradient, (0, bar_top), gradient)
    img = img_rgba.convert("RGB")
    draw = ImageDraw.Draw(img)

    # --- channel name text ---
    # Target: text occupies ~70% of image width, sits ~8% from bottom
    max_text_w = int(SIZE * 0.70)
    font_size  = int(SIZE * 0.065)   # start at 6.5% of canvas
    font       = find_font(font_size)

    # Shrink font until text fits horizontally
    while font_size > 40:
        font = find_font(font_size)
        bbox = draw.textbbox((0, 0), channel_name, font=font)
        text_w = bbox[2] - bbox[0]
        if text_w <= max_text_w:
            break
        font_size = int(font_size * 0.9)

    bbox    = draw.textbbox((0, 0), channel_name, font=font)
    text_w  = bbox[2] - bbox[0]
    text_h  = bbox[3] - bbox[1]
    text_x  = (SIZE - text_w) // 2
    text_y  = SIZE - int(SIZE * 0.10) - text_h   # 10% from bottom

    # Shadow
    shadow_offset = max(3, font_size // 30)
    draw.text((text_x + shadow_offset, text_y + shadow_offset),
              channel_name, font=font, fill=(0, 0, 0, 180))
    # Main text
    draw.text((text_x, text_y), channel_name, font=font, fill=(255, 255, 255))

    # --- save: RGB PNG, no alpha, 72 dpi ---
    img.save(str(out_path), format="PNG", dpi=(DPI, DPI))
    print(f"  Saved -> {out_path}  ({SIZE}x{SIZE} px)")


def main():
    parser = argparse.ArgumentParser(description="Generate podcast artwork from YouTube thumbnails")
    parser.add_argument("--force", action="store_true", help="Regenerate even if artwork already exists")
    args = parser.parse_args()

    api_key = os.environ.get("YOUTUBE_API_KEY")
    if not api_key:
        print("ERROR: YOUTUBE_API_KEY environment variable is not set.")
        sys.exit(1)

    ARTWORK_DIR.mkdir(exist_ok=True)

    channels = json.loads(Path(CHANNELS_FILE).read_text())
    generated = 0
    skipped   = 0
    errors    = []

    for ch in channels:
        if not ch.get("enabled", True):
            continue

        slug       = ch["slug"]
        out_path   = ARTWORK_DIR / f"{slug}.png"

        if out_path.exists() and not args.force:
            print(f"[{slug}] artwork exists — skipping (use --force to regenerate)")
            skipped += 1
            continue

        print(f"\n[{slug}]")
        try:
            thumb_bytes  = fetch_best_thumbnail(ch["youtube_channel_id"], api_key)
            channel_name = ch.get("podcast_author", slug)
            generate_artwork(channel_name, thumb_bytes, out_path)
            generated += 1
        except Exception as e:
            print(f"  ERROR: {e}")
            errors.append((slug, str(e)))

    print(f"\n=== Done: {generated} generated, {skipped} skipped, {len(errors)} errors ===")
    if errors:
        for slug, err in errors:
            print(f"  {slug}: {err}")
        sys.exit(1)


if __name__ == "__main__":
    main()
