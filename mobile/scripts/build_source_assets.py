"""
Build the SOURCE app assets (icon + splash) for @capacitor/assets from the
existing site brand logo (../../favicon.png — navy #1a1a2e + gold line-art:
Torah scroll + microphone + soundwaves + "THE TORAH PODCAST" wordmark).

Outputs (consumed by `npm run assets` -> capacitor-assets generate):
  mobile/resources/icon.png        1024x1024  opaque, no alpha (Apple requirement)
  mobile/resources/splash.png      2732x2732  navy bg + white card w/ emblem
  mobile/resources/splash-dark.png 2732x2732  same (site is dark-navy themed)

The app ICON drops the wordmark (illegible at icon sizes) and keeps only the
emblem on white, matching the favicon's own white field. The SPLASH centers the
emblem on a white rounded card over the brand navy so the navy line-art stays
readable on a dark screen.

Deterministic: re-running reproduces byte-identical intent from favicon.png.
David can drop in a bespoke visual later and re-run `npm run assets`.

Run:  py mobile/scripts/build_source_assets.py   (from repo root)
"""
from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "favicon.png"
RES = ROOT / "mobile" / "resources"
RES.mkdir(parents=True, exist_ok=True)

NAVY = (26, 26, 46)      # #1a1a2e  — site theme-color / brand navy
WHITE = (255, 255, 255)

# Emblem bounding box inside favicon.png (excludes the wordmark). See detection
# in the report; recomputed defensively below if the source changes.
def emblem_bbox(im: Image.Image) -> tuple[int, int, int, int]:
    w, h = im.size
    px = im.convert("RGB").load()
    top = int(h * 0.70)  # wordmark sits in the bottom ~30%
    thr = 235
    minx, miny, maxx, maxy = w, h, 0, 0
    for y in range(top):
        for x in range(w):
            r, g, b = px[x, y]
            if r < thr or g < thr or b < thr:
                minx, maxx = min(minx, x), max(maxx, x)
                miny, maxy = min(miny, y), max(maxy, y)
    return minx, miny, maxx, maxy


def emblem(pad_ratio: float = 0.06) -> Image.Image:
    im = Image.open(SRC).convert("RGB")
    l, t, r, b = emblem_bbox(im)
    pad = int(max(r - l, b - t) * pad_ratio)
    l, t = max(0, l - pad), max(0, t - pad)
    r, b = min(im.width, r + pad), min(im.height, b + pad)
    return im.crop((l, t, r, b))


def build_icon(size: int = 1024, frac: float = 0.80) -> None:
    canvas = Image.new("RGB", (size, size), WHITE)
    em = emblem()
    scale = (size * frac) / max(em.size)
    em = em.resize((round(em.width * scale), round(em.height * scale)), Image.LANCZOS)
    canvas.paste(em, ((size - em.width) // 2, (size - em.height) // 2))
    canvas.save(RES / "icon.png")
    print(f"  icon.png        {size}x{size} (opaque, no alpha)")


def rounded_card(box: int, radius: int) -> Image.Image:
    card = Image.new("RGBA", (box, box), (255, 255, 255, 255))
    mask = Image.new("L", (box, box), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, box - 1, box - 1), radius=radius, fill=255)
    card.putalpha(mask)
    return card


def build_splash(name: str, size: int = 2732) -> None:
    canvas = Image.new("RGB", (size, size), NAVY)
    box = round(size * 0.46)
    card = rounded_card(box, radius=round(box * 0.16))
    em = emblem()
    scale = (box * 0.80) / max(em.size)
    em = em.resize((round(em.width * scale), round(em.height * scale)), Image.LANCZOS)
    card.paste(em, ((box - em.width) // 2, (box - em.height) // 2))
    off = (size - box) // 2
    canvas.paste(card, (off, off), card)
    canvas.save(RES / name)
    print(f"  {name:<15} {size}x{size} (navy #1a1a2e + white card)")


if __name__ == "__main__":
    print("Building source app assets from", SRC.name)
    build_icon()
    build_splash("splash.png")
    build_splash("splash-dark.png")
    print("Done ->", RES)
