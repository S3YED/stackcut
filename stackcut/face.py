"""The talking head: find the face, follow it smoothly, frame it, fix the room light."""
from __future__ import annotations

import hashlib
import json
import urllib.request
from pathlib import Path

import cv2
import numpy as np

from . import media

YUNET_URL = ("https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/"
             "face_detection_yunet_2023mar.onnx")
YUNET_SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"
CACHE = Path.home() / ".cache" / "stackcut"


def yunet_model() -> Path:
    """OpenCV Zoo's YuNet face detector (MIT licence), fetched once into ~/.cache/stackcut."""
    path = CACHE / "face_detection_yunet_2023mar.onnx"
    if not path.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        print("  downloading the YuNet face detector (230 KB, once)")
        urllib.request.urlretrieve(YUNET_URL, path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != YUNET_SHA256:
        path.unlink()
        raise SystemExit(f"YuNet model checksum mismatch ({digest}); delete {path} and try again")
    return path


def track(video: Path, out: Path, rate: float = 10.0) -> dict:
    """Detect the biggest face `rate` times a second. Cached as JSON: {w, h, samples: [{t, x, y, fh}]}."""
    if out.exists():
        return json.loads(out.read_text())
    w, h = media.size(video)
    scale = min(1.0, 640 / max(w, h))
    sw, sh = int(w * scale), int(h * scale)
    det = cv2.FaceDetectorYN.create(str(yunet_model()), "", (sw, sh), 0.7, 0.3, 5000)
    n = int(media.duration(video) * rate)
    samples = []
    cap = cv2.VideoCapture(str(video))
    for i in range(n):
        t = i / rate
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = cap.read()
        if not ok:
            break
        _, faces = det.detect(cv2.resize(frame, (sw, sh)))
        if faces is not None and len(faces):
            x, y, fw, fh = max(faces, key=lambda f: f[2] * f[3])[:4] / scale
            samples.append({"t": round(t, 3), "x": float(x + fw / 2), "y": float(y + fh / 2), "fh": float(fh)})
    cap.release()
    if not samples:
        raise SystemExit(f"no face found in {video.name}")
    data = {"w": w, "h": h, "samples": samples}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data))
    print(f"  face found in {len(samples)} of {n} samples of {video.name}")
    return data


class Smooth:
    """A steady virtual camera: centre smoothed over about 1.3 s, size over about 3 s."""

    def __init__(self, data: dict, rate: float = 10.0):
        s = data["samples"]
        self.t = np.array([x["t"] for x in s])

        def avg(a: np.ndarray, k: int) -> np.ndarray:
            k = max(1, min(k, len(a) // 2 * 2 + 1))
            pad = np.pad(a, (k // 2, k // 2), mode="edge")
            return np.convolve(pad, np.ones(k) / k, mode="valid")

        k1, k2 = int(1.3 * rate) | 1, int(3.1 * rate) | 1
        self.x = avg(np.array([x["x"] for x in s]), k1)
        self.y = avg(np.array([x["y"] for x in s]), k1)
        self.fh = avg(np.array([x["fh"] for x in s]), k2)

    def at(self, t: float) -> tuple[float, float, float]:
        return (float(np.interp(t, self.t, self.x)), float(np.interp(t, self.t, self.y)),
                float(np.interp(t, self.t, self.fh)))


def white_balance(video: Path, cam: Smooth, strength: float = 0.5) -> np.ndarray:
    """Per-channel LUT that pulls a coloured room light toward neutral, using the brighter half of
    the shirt under the face as the white reference. A dark shirt (no reference) leaves it alone."""
    if strength <= 0:
        return np.tile(np.arange(256, dtype=np.uint8)[:, None], (1, 3))
    w, h = media.size(video)
    refs = []
    for t in np.linspace(cam.t[0], cam.t[-1], 8)[1:-1]:
        frame = next(media.frames(video, float(t), 1, w, h))
        if frame is None:
            continue
        x, y, fh = cam.at(float(t))
        y0, y1 = int(y + 1.3 * fh), int(y + 2.0 * fh)
        x0, x1 = int(x - 0.7 * fh), int(x + 0.7 * fh)
        px = frame[max(y0, 0):min(y1, h), max(x0, 0):min(x1, w)].reshape(-1, 3).astype(np.float32)
        if len(px) > 100:
            lum = px.mean(1)
            refs.append(px[lum > np.percentile(lum, 50)].mean(0))
    ref = np.mean(refs, axis=0) if refs else np.array([128.0, 128.0, 128.0])
    gains = (ref.mean() / np.maximum(ref, 1)) ** strength if ref.mean() > 90 else np.ones(3)
    x = np.arange(256, dtype=np.float32)
    return np.stack([np.clip(x * g, 0, 255) for g in gains], 1).astype(np.uint8)


def apply_lut(frame: np.ndarray, lut: np.ndarray) -> np.ndarray:
    return cv2.merge([cv2.LUT(c, lut[:, j].copy()) for j, c in enumerate(cv2.split(frame))])


def panel(frame: np.ndarray, cam: tuple[float, float, float], pw: int, ph: int,
          fill: float = 0.36, eye_line: float = 0.44) -> np.ndarray:
    """Frame the face: it fills `fill` of the panel height, centred, eyes at `eye_line`.
    The crop never leaves the picture; a face near the edge sits off-centre instead."""
    x, y, fh = cam
    sh, sw = frame.shape[:2]
    scale = max(fill * ph / max(fh, 1), pw / sw, ph / sh)
    cx = min(max(x, pw / 2 / scale), sw - pw / 2 / scale)
    cy = min(max(y, ph * eye_line / scale), sh - ph * (1 - eye_line) / scale)
    m = np.float32([[scale, 0, pw / 2 - cx * scale], [0, scale, ph * eye_line - cy * scale]])
    return cv2.warpAffine(frame, m, (pw, ph), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
