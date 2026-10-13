"""Fast visual pre-screen that runs before the vision-language model.

Two modes, the same ones used by real automated optical inspection (AOI) stations:
  * golden-sample differencing - when a reference image of a good part exists (data/golden/)
  * local-anomaly map          - for arbitrary photos: residual from a smoothed copy + colour cues

Output is a set of candidate regions (bbox, type guess, score) plus an overlay PNG for the UI.
The VLM gets these as *hints* and is told they may be wrong; the final call is the model's,
and in offline demo mode the reasoner uses them directly.
"""
from __future__ import annotations

import base64
import io
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from scipy import ndimage

from .config import settings

WORK_W = 320
TYPE_COLORS = {
    "scratch": (255, 196, 0), "crack": (255, 64, 64), "corrosion": (255, 120, 40),
    "missing_component": (190, 90, 255), "dent": (0, 200, 255), "surface_anomaly": (255, 255, 255),
}


def _load(img_bytes: bytes) -> Image.Image:
    im = Image.open(io.BytesIO(img_bytes))
    im = im.convert("RGB")
    h = int(im.height * WORK_W / im.width)
    return im.resize((WORK_W, h), Image.BILINEAR)


def _golden_for(shape: tuple[int, int]) -> np.ndarray | None:
    gdir = settings.data_dir / "golden"
    for p in sorted(gdir.glob("*.png")) if gdir.exists() else []:
        g = _load(p.read_bytes())
        if g.size == (shape[1], shape[0]):
            return np.asarray(g.filter(ImageFilter.GaussianBlur(1.0)), dtype=np.float32) / 255
    return None


def _location(cx: float, cy: float) -> str:
    v = "upper" if cy < 0.38 else "lower" if cy > 0.62 else "center"
    h = "left" if cx < 0.38 else "right" if cx > 0.62 else "center"
    if v == h == "center":
        return "center"
    return f"{v}-{h}" if v != "center" and h != "center" else (v if h == "center" else f"center-{h}")


def inspect(img_bytes: bytes) -> dict:
    im = _load(img_bytes)
    rgb = np.asarray(im.filter(ImageFilter.GaussianBlur(1.0)), dtype=np.float32) / 255
    H, W, _ = rgb.shape
    gray = rgb.mean(-1)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    rust = np.clip((r - b) - 0.12, 0, None) * (r > g) * (g > b)

    golden = _golden_for((H, W))
    if golden is not None:
        method = "golden-sample differencing"
        diff = np.abs(rgb - golden).max(-1)
        signed = gray - golden.mean(-1)
        anomaly = np.clip((diff - 0.06) / 0.18, 0, 1)
    else:
        method = "local anomaly map"
        smooth = ndimage.gaussian_filter(gray, 6)
        resid = gray - smooth
        signed = resid
        mad = np.median(np.abs(resid - np.median(resid))) * 1.4826 + 1e-4
        anomaly = np.clip((np.abs(resid) / mad - 5) / 10, 0, 1)
    anomaly = np.maximum(anomaly, np.clip(rust / 0.25, 0, 1))
    margin = max(4, W // 40)
    anomaly[:margin], anomaly[-margin:], anomaly[:, :margin], anomaly[:, -margin:] = 0, 0, 0, 0

    core = anomaly > 0.25
    mask = ndimage.binary_dilation(core, iterations=4)
    labels, n = ndimage.label(mask)
    regions = []
    for i in range(1, n + 1):
        sel = (labels == i) & core
        ys, xs = np.where(sel)
        area = len(xs)
        if area < 12:
            continue
        strength = float(np.percentile(anomaly[ys, xs], 75))
        x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
        # elongation via PCA of pixel coordinates
        cov = np.cov(np.vstack([xs, ys]).astype(float)) if area > 2 else np.eye(2)
        ev = np.sort(np.linalg.eigvalsh(cov))[::-1]
        elong = float(np.sqrt(ev[0] / max(ev[1], 1e-3)))
        rust_frac = float((rust[ys, xs] > 0.04).mean())
        tone = float(signed[ys, xs].mean())
        dark = float(np.percentile(gray[ys, xs], 25))
        fill = area / max(1, (x1 - x0 + 1) * (y1 - y0 + 1))
        if rust_frac > 0.3:
            kind = "corrosion"
        elif elong > 3.2 and tone > -0.02:
            kind = "scratch"
        elif elong > 3.2:
            kind = "crack"
        elif dark < 0.3 and fill > 0.45:
            kind = "missing_component"
        elif elong <= 3.2:
            kind = "dent" if golden is not None else "surface_anomaly"
        else:
            kind = "surface_anomaly"
        extent = max(x1 - x0, y1 - y0) / max(H, W)
        score = float(np.clip(strength * 0.55 + min(area / (H * W) * 40, 0.25) + min(extent, 0.4) * 0.5, 0, 1))
        cx, cy = (x0 + x1) / 2 / W, (y0 + y1) / 2 / H
        regions.append({
            "type": kind, "score": round(score, 2), "location": _location(cx, cy),
            "bbox": [round(x0 / W, 3), round(y0 / H, 3), round((x1 + 1) / W, 3), round((y1 + 1) / H, 3)],
            "area_pct": round(100 * area / (H * W), 2), "elongation": round(elong, 1),
        })
    regions.sort(key=lambda d: d["score"], reverse=True)
    regions = regions[:5]
    anomaly_score = round(max([r["score"] for r in regions], default=0.0), 2)
    return {
        "method": method,
        "defect_suspected": anomaly_score >= 0.3,
        "anomaly_score": anomaly_score,
        "regions": regions,
        "overlay_png": _overlay(im, anomaly, regions),
        "summary": _summary(regions, method),
    }


def _summary(regions: list[dict], method: str) -> str:
    if not regions:
        return f"Pre-screen ({method}): no visual anomaly above threshold."
    parts = [f"{r['type'].replace('_', ' ')} at {r['location']} (score {r['score']}, {r['area_pct']}% of frame)"
             for r in regions]
    return f"Pre-screen ({method}): " + "; ".join(parts) + "."


def _overlay(im: Image.Image, anomaly: np.ndarray, regions: list[dict]) -> str:
    base = im.convert("RGBA").resize((im.width * 2, im.height * 2), Image.BILINEAR)
    heat = np.zeros((*anomaly.shape, 4), np.uint8)
    a = ndimage.gaussian_filter(anomaly, 2)
    heat[..., 0] = 255
    heat[..., 1] = (60 + 140 * (1 - a)).astype(np.uint8)
    heat[..., 3] = (np.clip(a * 1.6, 0, 1) * 150).astype(np.uint8)
    heat_img = Image.fromarray(heat, "RGBA").resize(base.size, Image.BILINEAR)
    out = Image.alpha_composite(base, heat_img)
    d = ImageDraw.Draw(out)
    W, H = out.size
    for r in regions:
        x0, y0, x1, y1 = r["bbox"]
        pad = 6
        col = TYPE_COLORS.get(r["type"], (255, 255, 255))
        box = [x0 * W - pad, y0 * H - pad, x1 * W + pad, y1 * H + pad]
        d.rectangle(box, outline=col + (255,), width=3)
        label = f" {r['type'].replace('_', ' ')} {r['score']:.2f} "
        tw = d.textlength(label)
        ty = box[1] - 16 if box[1] > 18 else box[3] + 2
        d.rectangle([box[0], ty, box[0] + tw, ty + 15], fill=col + (235,))
        d.text((box[0], ty + 1), label, fill=(10, 10, 12, 255))
    buf = io.BytesIO()
    out.convert("RGB").save(buf, "JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def golden_path() -> Path:
    return settings.data_dir / "golden" / "part_ok.png"
