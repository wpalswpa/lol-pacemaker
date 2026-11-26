"""App icon (AGENTS §17): rounded orange square with "PACE / MAKER".

Run once when the mark changes (Pillow is not a project dependency):
    uv run --with pillow python scripts/make_icon.py
Writes assets/branding/icon.png (1024), icon.ico (Windows), icon.icns (macOS, via iconutil).
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "branding"
ORANGE = (255, 90, 31, 255)  # AGENTS §0 brand orange #FF5A1F
WHITE = (255, 255, 255, 255)
FONTS = ["/System/Library/Fonts/Supplemental/Arial Black.ttf", "C:/Windows/Fonts/ariblk.ttf"]


def render(size: int = 1024) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = round(size * 0.04)
    d.rounded_rectangle((pad, pad, size - pad, size - pad), radius=round(size * 0.22), fill=ORANGE)
    font_path = next((f for f in FONTS if Path(f).exists()), None)
    if font_path is None:
        sys.exit("Arial Black not found; set FONTS to a heavy sans font")
    font = ImageFont.truetype(font_path, round(size * 0.235))
    lines = ["PACE", "MAKER"]
    boxes = [d.textbbox((0, 0), t, font=font) for t in lines]
    widest = max(b[2] - b[0] for b in boxes)
    if widest > size * 0.78:  # shrink to keep a safe margin
        font = ImageFont.truetype(font_path, round(size * 0.235 * size * 0.78 / widest))
        boxes = [d.textbbox((0, 0), t, font=font) for t in lines]
    heights = [b[3] - b[1] for b in boxes]
    gap = round(size * 0.04)
    y = (size - (sum(heights) + gap)) / 2
    for t, b, h in zip(lines, boxes, heights):
        # stroke in the same colour thickens Arial Black further so it stays legible at 64 px
        d.text(((size - (b[2] - b[0])) / 2 - b[0], y - b[1]), t, font=font, fill=WHITE,
               stroke_width=round(size * 0.008), stroke_fill=WHITE)
        y += h + gap
    return img


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    img = render()
    img.save(OUT / "icon.png")
    img.resize((64, 64), Image.LANCZOS).save(OUT / "hud_bubble.png")  # folded HUD button
    img.save(OUT / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    if shutil.which("iconutil"):
        with tempfile.TemporaryDirectory() as tmp:
            iconset = Path(tmp) / "icon.iconset"
            iconset.mkdir()
            for s in (16, 32, 128, 256, 512):
                img.resize((s, s), Image.LANCZOS).save(iconset / f"icon_{s}x{s}.png")
                img.resize((s * 2, s * 2), Image.LANCZOS).save(iconset / f"icon_{s}x{s}@2x.png")
            subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(OUT / "icon.icns")], check=True)
    print("wrote", sorted(p.name for p in OUT.iterdir()))


if __name__ == "__main__":
    main()
