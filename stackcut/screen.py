"""The screen half: a rounded, shadowed window on a soft gradient, a camera that eases to what
you're talking about, automatic zoom targets from on-screen activity, and privacy blur."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from . import media


def hex_bgr(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return b, g, r


def background(w: int, h: int, colors: list[str], base: str) -> np.ndarray:
    """Soft blurred gradient wallpaper: three colour glows on a dark base, plus a little grain."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    img = np.ones((h, w, 3), np.float32) * np.array(hex_bgr(base), np.float32)
    spots = [((0.17, 0.14), 0.48, 0.55), ((0.86, 0.33), 0.52, 0.40), ((0.52, 0.55), 0.59, 0.35)]
    for ((fx, fy), fr, a), col in zip(spots, colors):
        cx, cy, r = fx * w, fy * h, fr * w
        g = np.exp(-(((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * r * r)))[..., None] * a
        img = img * (1 - g) + np.array(hex_bgr(col), np.float32) * g
    grain = np.random.default_rng(7).normal(0, 2.0, (h, w, 1)).astype(np.float32)
    return np.clip(img + grain, 0, 255).astype(np.uint8)


def rounded_mask(w: int, h: int, r: int, ss: int = 4) -> np.ndarray:
    big = np.zeros((h * ss, w * ss), np.uint8)
    R = r * ss
    cv2.rectangle(big, (R, 0), (w * ss - R, h * ss), 255, -1)
    cv2.rectangle(big, (0, R), (w * ss, h * ss - R), 255, -1)
    for cx, cy in ((R, R), (w * ss - R - 1, R), (R, h * ss - R - 1), (w * ss - R - 1, h * ss - R - 1)):
        cv2.circle(big, (cx, cy), R, 255, -1)
    return cv2.resize(big, (w, h), interpolation=cv2.INTER_AREA).astype(np.float32)[..., None] / 255


class Window:
    """Pre-computes the static parts (background with baked-in shadow, masks) once."""

    def __init__(self, canvas_w: int, canvas_h: int, x: int, y: int, w: int, h: int, radius: int,
                 colors: list[str], base: str):
        self.x, self.y, self.w, self.h = x, y, w, h
        bg = background(canvas_w, canvas_h, colors, base)
        a = rounded_mask(w, h, radius)[..., 0]
        shadow = np.zeros((canvas_h, canvas_w), np.float32)
        shadow[y + 18:y + 18 + h, x:x + w] = a[: max(0, min(h, canvas_h - y - 18))]
        shadow = cv2.GaussianBlur(shadow, (0, 0), 26) * 0.75
        self.bg = (bg * (1 - shadow[..., None])).astype(np.uint8)
        self.mask = rounded_mask(w, h, radius)
        inner = np.pad(rounded_mask(w - 4, h - 4, max(1, radius - 2)), ((2, 2), (2, 2), (0, 0)))
        self.edge = (self.mask - inner) * 0.35

    def draw(self, canvas: np.ndarray, view: np.ndarray) -> None:
        roi = canvas[self.y:self.y + self.h, self.x:self.x + self.w].astype(np.float32)
        roi = view.astype(np.float32) * self.mask + roi * (1 - self.mask)
        roi = roi * (1 - self.edge) + 255 * self.edge
        canvas[self.y:self.y + self.h, self.x:self.x + self.w] = roi.astype(np.uint8)


def ease(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return 4 * x ** 3 if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2


def view(frame: np.ndarray, cx: float, cy: float, z: float, ww: int, wh: int, top: float = 0.0) -> np.ndarray:
    """The part of the screen the camera sees, scaled into the window.
    cx, cy: centre as fractions of the frame; z: zoom (1 = whole screen).
    top: a fraction of the frame's top that never shows (a browser's tabs and address bar)."""
    sh, sw = frame.shape[:2]
    z = max(z, 1 / (1 - top)) if top else z
    bw = min(sw, sh * ww / wh)               # z = 1: the widest part of the frame with the window's shape
    w, h = bw / z, bw * wh / ww / z          # (a portrait phone angle in a landscape window: a band, moved by cy)
    x0 = min(max(cx * sw - w / 2, 0), sw - w)
    y0 = min(max(cy * sh - h / 2, top * sh), sh - h)
    s = ww / w
    src, k = frame, 1.0
    while s * k < 0.75:      # shrink with area filtering first: no shimmering text when zoomed out
        src = cv2.resize(src, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
        k *= 2
    sk = s * k
    m = np.float32([[sk, 0, -x0 / k * sk], [0, sk, -y0 / k * sk]])
    return cv2.warpAffine(src, m, (ww, wh), flags=cv2.INTER_CUBIC if s > 1 else cv2.INTER_LINEAR)


def auto_zoom(video: Path, t0: float, t1: float, max_zoom: float = 2.2, top_crop: float = 0.0) -> list[float]:
    """Where the action is between t0 and t1: frame-difference activity at 4 fps.
    Little activity or a whole-page change gives the full view; otherwise the camera frames the
    region that moved (cursor, typing, a panel that updated), with some air around it."""
    w, h = media.size(video)
    sw, sh = 480, int(480 * h / w)
    n = max(2, int((t1 - t0) * 4))
    heat = np.zeros((sh, sw), np.float32)
    prev, big = None, 0
    cap = cv2.VideoCapture(str(video))
    for i in range(n):
        cap.set(cv2.CAP_PROP_POS_MSEC, (t0 + i * (t1 - t0) / n) * 1000)
        ok, f = cap.read()
        if not ok:
            break
        g = cv2.cvtColor(cv2.resize(f, (sw, sh)), cv2.COLOR_BGR2GRAY).astype(np.int16)
        if prev is not None:
            d = np.abs(g - prev) > 25
            if d.mean() > 0.25:      # page switch: not a focus point
                big += 1
            else:
                heat += d
        prev = g
    cap.release()
    full = [0.5, 1.0 if top_crop else 0.5, 1.0 / (1.0 - top_crop) if top_crop else 1.0]
    if heat.sum() < 40 or big > n // 3:
        return full
    ys, xs = np.nonzero(heat)
    wts = heat[ys, xs]
    x0, x1 = np.percentile(np.repeat(xs, wts.astype(int)), [5, 95])
    y0, y1 = np.percentile(np.repeat(ys, wts.astype(int)), [5, 95])
    bw, bh = (x1 - x0) / sw * 1.5 + 0.12, (y1 - y0) / sh * 1.5 + 0.12
    z = float(np.clip(min(1 / bw, 1 / bh), 1.0, max_zoom))
    cx, cy = (x0 + x1) / 2 / sw, (y0 + y1) / 2 / sh
    if top_crop:   # keep the browser chrome out of frame
        cy = max(cy, top_crop + 0.5 / z)
    return [round(float(cx), 3), round(float(cy), 3), round(z, 2)]


def blur(frame: np.ndarray, rects: list[tuple[int, int, int, int]]) -> np.ndarray:
    """Heavy, unreadable blur (pixelate, then soften) over [x, y, w, h] rectangles in screen pixels."""
    out = frame.copy()
    for x, y, w, h in rects:
        x, y = max(0, int(x)), max(0, int(y))
        w, h = min(int(w), out.shape[1] - x), min(int(h), out.shape[0] - y)
        if w < 2 or h < 2:
            continue
        roi = out[y:y + h, x:x + w]
        small = cv2.resize(roi, (max(2, w // 14), max(2, h // 14)), interpolation=cv2.INTER_AREA)
        soft = cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)
        out[y:y + h, x:x + w] = (cv2.blur(soft, (13, 13)) * 0.96).astype(np.uint8)
    return out
