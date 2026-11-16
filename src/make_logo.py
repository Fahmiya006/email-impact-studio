"""Turn assets/logo-original.png into the round logo used by the app: sidebar, sign-in, browser tab, report letterhead.
The artwork is centred on a white disc just large enough to hold every part of the drawing, then saved at several sizes."""
from pathlib import Path
from PIL import Image, ImageChops, ImageDraw
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "app" / "static"

def build():
    src = Image.open(ROOT / "assets" / "logo-original.png").convert("RGBA")
    bg = Image.new("RGBA", src.size, (255, 255, 255, 255)); bg.alpha_composite(src); rgb = bg.convert("RGB")
    a = np.asarray(rgb).astype(int)
    ink = (255 * 3 - a.sum(axis=2)) > 60                   # anything that is not near-white paper
    ys, xs = np.where(ink)
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    pts = np.stack([xs[::7], ys[::7]], axis=1).astype(float)  # smallest circle that holds every drawn pixel
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    step = max(x1 - x0, y1 - y0) / 8
    while step > .5:
        best = (np.hypot(pts[:, 0] - cx, pts[:, 1] - cy).max(), cx, cy)
        for dx, dy in ((step, 0), (-step, 0), (0, step), (0, -step), (step, step), (-step, -step), (step, -step), (-step, step)):
            d = np.hypot(pts[:, 0] - (cx + dx), pts[:, 1] - (cy + dy)).max()
            if d < best[0]:
                best = (d, cx + dx, cy + dy)
        if (best[1], best[2]) == (cx, cy):
            step /= 2
        else:
            cx, cy = best[1], best[2]
    r = np.hypot(pts[:, 0] - cx, pts[:, 1] - cy).max() * 1.08  # a thin margin around the drawing
    side = int(r * 2)
    paper = Image.new("RGB", (side, side), (255, 255, 255))
    paper.paste(rgb, (int(side / 2 - cx), int(side / 2 - cy)))
    yy, xx = np.mgrid[0:side, 0:side]
    d = np.clip(np.hypot(xx - side / 2, yy - side / 2) / (side / 2), 0, 1)   # soft lavender glow toward the edge so the disc reads as round
    tint = np.stack([255 - 22 * d ** 2, 255 - 30 * d ** 2, 255 - 4 * d ** 2], axis=2).astype("uint8")
    canvas = ImageChops.multiply(paper, Image.fromarray(tint, "RGB")).convert("RGBA")
    scale = 4                                               # supersample the circle edge so it is smooth
    mask = Image.new("L", (side * scale, side * scale), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, side * scale - 1, side * scale - 1), fill=255)
    mask = mask.resize((side, side), Image.LANCZOS)
    canvas.putalpha(mask)
    for name, px in (("logo-512.png", 512), ("logo-128.png", 128), ("apple-touch-icon.png", 180), ("favicon-64.png", 64)):
        canvas.resize((px, px), Image.LANCZOS).save(OUT / name, optimize=True)
    canvas.resize((64, 64), Image.LANCZOS).save(OUT / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
    print("wrote round logo files", side)

if __name__ == "__main__":
    build()
