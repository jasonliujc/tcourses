"""Generate the app icon: a red circle with a white "T".

    pip install Pillow
    python packaging/make_icon.py            # filled circle, white T (default)
    python packaging/make_icon.py --ring     # red ring with a red T

Writes packaging/icon.png, icon.ico (Windows), icon.icns (macOS) and
frontend/public/favicon.svg. The outputs are committed, so building the app
does not need Pillow.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
RED = "#E72429"
WHITE = "#FFFFFF"
SIZE = 1024
SUPERSAMPLE = 4

# Geometry on a 1024 grid
CENTER, OUTER, RING = 512, 470, 92
RING_T = {"bar": (312, 300, 712, 392), "stem": (462, 300, 562, 724)}
SOLID_T = {"bar": (292, 290, 732, 392), "stem": (457, 290, 567, 744)}
RADIUS = 14


def render(solid: bool) -> Image.Image:
    s = SUPERSAMPLE
    img = Image.new("RGBA", (SIZE * s, SIZE * s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    box = [(CENTER - OUTER) * s, (CENTER - OUTER) * s, (CENTER + OUTER) * s, (CENTER + OUTER) * s]
    if solid:
        draw.ellipse(box, fill=RED)
        t, color = SOLID_T, WHITE
    else:
        draw.ellipse(box, outline=RED, width=RING * s)
        t, color = RING_T, RED
    for rect in t.values():
        draw.rounded_rectangle([v * s for v in rect], radius=RADIUS * s, fill=color)
    return img.resize((SIZE, SIZE), Image.LANCZOS)


def svg(solid: bool) -> str:
    t = SOLID_T if solid else RING_T
    color = WHITE if solid else RED
    if solid:
        circle = f'<circle cx="{CENTER}" cy="{CENTER}" r="{OUTER}" fill="{RED}"/>'
    else:
        r = OUTER - RING / 2
        circle = (f'<circle cx="{CENTER}" cy="{CENTER}" r="{r:g}" fill="none" '
                  f'stroke="{RED}" stroke-width="{RING}"/>')
    rects = "".join(
        f'<rect x="{x0}" y="{y0}" width="{x1 - x0}" height="{y1 - y0}" rx="{RADIUS}" fill="{color}"/>'
        for x0, y0, x1, y1 in t.values()
    )
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {SIZE} {SIZE}">{circle}{rects}</svg>\n'


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ring", action="store_true")
    parser.add_argument("--out", type=Path, default=ROOT / "packaging")
    args = parser.parse_args()

    solid = not args.ring
    icon = render(solid)
    args.out.mkdir(parents=True, exist_ok=True)
    icon.save(args.out / "icon.png")
    icon.save(args.out / "icon.ico", sizes=[(n, n) for n in (16, 24, 32, 48, 64, 128, 256)])
    icon.save(args.out / "icon.icns")
    if args.out == ROOT / "packaging":
        (ROOT / "frontend" / "public").mkdir(exist_ok=True)
        (ROOT / "frontend" / "public" / "favicon.svg").write_text(svg(solid), encoding="utf-8")
    print("wrote", ", ".join(p.name for p in sorted(args.out.glob("icon.*"))))


if __name__ == "__main__":
    main()
