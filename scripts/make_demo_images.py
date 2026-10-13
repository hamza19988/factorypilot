"""Generate synthetic inspection photos (normal + defective) for the demo.

Real deployments use line cameras or a technician's phone; for the public demo we
ship procedurally generated, licence-free images of machined steel parts so the
repo runs out of the box. (MVTec AD images can be dropped into data/samples/ too.)

Usage: python scripts/make_demo_images.py --out data/samples
"""
import argparse
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

W, H = 640, 480


def _brushed_steel(rng: np.random.Generator, tint=(1.0, 1.0, 1.0)) -> np.ndarray:
    noise = rng.normal(0, 1, (H, W))
    img = Image.fromarray(((noise - noise.min()) / np.ptp(noise) * 255).astype(np.uint8))
    img = img.filter(ImageFilter.BoxBlur(1)).resize((W * 4, H), Image.BILINEAR)
    img = img.filter(ImageFilter.GaussianBlur((18, 0))) if hasattr(ImageFilter, "GaussianBlur") else img
    streak = np.asarray(img.resize((W, H)), dtype=np.float32) / 255
    yy, xx = np.mgrid[0:H, 0:W]
    light = 0.62 + 0.25 * np.exp(-(((xx - W * 0.35) / (W * 0.6)) ** 2 + ((yy - H * 0.3) / (H * 0.7)) ** 2))
    base = light + (streak - 0.5) * 0.10
    rgb = np.stack([base * tint[0] * 0.93, base * tint[1] * 0.95, base * tint[2] * 1.0], -1)
    return np.clip(rgb, 0, 1)


def _part(rng, tint=(1, 1, 1)) -> Image.Image:
    bg = np.zeros((H, W, 3), np.float32) + np.array([0.12, 0.13, 0.15])
    bg += rng.normal(0, 0.01, bg.shape)
    steel = _brushed_steel(rng, tint)
    mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mask).rounded_rectangle([50, 50, W - 50, H - 50], radius=28, fill=255)
    m = np.asarray(mask.filter(ImageFilter.GaussianBlur(1.2)), dtype=np.float32)[..., None] / 255
    arr = bg * (1 - m) + steel * m
    img = Image.fromarray((arr * 255).astype(np.uint8))
    d = ImageDraw.Draw(img)
    # bevel highlight / shadow
    d.rounded_rectangle([50, 50, W - 50, H - 50], radius=28, outline=(220, 224, 230), width=2)
    d.rounded_rectangle([53, 53, W - 47, H - 47], radius=28, outline=(40, 42, 46), width=2)
    # machined pocket
    d.rounded_rectangle([230, 180, 410, 300], radius=14, fill=(118, 122, 128), outline=(70, 72, 78), width=3)
    d.line([234, 296, 406, 296], fill=(190, 194, 200), width=2)
    return img


BOLTS = [(95, 95), (W - 95, 95), (95, H - 95), (W - 95, H - 95)]


def _bolt(d: ImageDraw.ImageDraw, x, y, present=True):
    if present:
        d.ellipse([x - 22, y - 22, x + 22, y + 22], fill=(150, 154, 160), outline=(60, 62, 66), width=3)
        d.ellipse([x - 16, y - 16, x + 16, y + 16], fill=(182, 186, 192))
        d.polygon([(x - 9, y - 5), (x, y - 10), (x + 9, y - 5), (x + 9, y + 5), (x, y + 10), (x - 9, y + 5)],
                  fill=(95, 98, 104))
    else:  # empty threaded hole
        d.ellipse([x - 13, y - 13, x + 13, y + 13], fill=(22, 22, 24), outline=(70, 72, 76), width=3)
        for r in (9, 6):
            d.ellipse([x - r, y - r, x + r, y + r], outline=(48, 48, 52), width=1)


def make(kind: str, seed: int) -> Image.Image:
    rng = np.random.default_rng(seed)
    img = _part(rng)
    d = ImageDraw.Draw(img)
    for i, (x, y) in enumerate(BOLTS):
        _bolt(d, x, y, present=not (kind == "missing_bolt" and i == 3))

    if kind == "scratch":
        x0, y0 = 120, 150
        pts = [(x0 + t * 3.1, y0 + t * 0.55 + rng.normal(0, 0.6)) for t in range(110)]
        d.line(pts, fill=(238, 240, 244), width=3)
        d.line([(x + 1, y + 3) for x, y in pts], fill=(70, 72, 76), width=2)
        pts2 = [(x0 + 20 + t * 2.6, y0 + 22 + t * 0.5) for t in range(70)]
        d.line(pts2, fill=(225, 228, 232), width=2)
    elif kind == "crack":
        x, y = W - 52, 140
        pts = [(x, y)]
        for _ in range(60):
            x -= rng.uniform(2.5, 5)
            y += rng.normal(1.2, 3.0)
            pts.append((x, y))
        d.line(pts, fill=(18, 18, 20), width=4)
        d.line([(px, py - 2) for px, py in pts], fill=(205, 208, 212), width=1)
        bx, by = pts[30]
        br = [(bx, by)]
        for _ in range(15):
            bx -= rng.uniform(1.5, 3.5)
            by += rng.uniform(1.5, 4)
            br.append((bx, by))
        d.line(br, fill=(25, 25, 28), width=2)
    elif kind == "corrosion":
        arr = np.asarray(img, dtype=np.float32) / 255
        yy, xx = np.mgrid[0:H, 0:W]
        field = np.zeros((H, W), np.float32)
        for _ in range(9):
            cx, cy = rng.uniform(170, 330), rng.uniform(310, 380)
            r = rng.uniform(14, 34)
            field += np.exp(-(((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * r * r)))
        speckle = rng.random((H, W)).astype(np.float32)
        field = np.clip(field * (0.7 + 0.6 * speckle), 0, 1)
        rust = np.stack([0.62 + 0.15 * speckle, 0.30 + 0.10 * speckle, 0.12 + 0.05 * speckle], -1)
        plate = np.zeros((H, W), np.float32)
        plate[60:H - 60, 60:W - 60] = 1
        a = (field ** 1.3 * plate)[..., None] * 0.9
        arr = arr * (1 - a) + rust * a
        img = Image.fromarray((np.clip(arr, 0, 1) * 255).astype(np.uint8))
    elif kind == "dent":
        arr = np.asarray(img, dtype=np.float32) / 255
        yy, xx = np.mgrid[0:H, 0:W]
        cx, cy, r = 470, 340, 40
        dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / r
        shade = np.where(dist < 1, (-0.16 * np.sign(xx - cx + 6) - 0.06) * np.clip(np.cos(dist * np.pi / 2), 0, 1) ** 0.7, 0)
        arr = arr + shade[..., None]
        img = Image.fromarray((np.clip(arr, 0, 1) * 255).astype(np.uint8))

    # camera noise + slight blur so it looks like a photo, not vector art
    arr = np.asarray(img, dtype=np.float32)
    arr += rng.normal(0, 3.0, arr.shape)
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.6))


SAMPLES = {
    "conveyor_c2_scratch.png": ("scratch", 11),
    "press_p1_crack.png": ("crack", 12),
    "cnc_m4_corrosion.png": ("corrosion", 13),
    "packaging_l3_missing_bolt.png": ("missing_bolt", 14),
    "packaging_l3_dent.png": ("dent", 16),
    "part_ok.png": ("ok", 15),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/samples")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, (kind, seed) in SAMPLES.items():
        make(kind, seed).save(out / name, optimize=True)
        print("wrote", out / name)


if __name__ == "__main__":
    main()
