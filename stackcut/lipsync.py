"""Lip sync, measured: how far a take's sound lags its picture.

A camera with a wireless or Bluetooth mic often records the sound a few frames late (the radio and
the mic's own processing take time, and the camera doesn't correct for it). On a DJI pocket camera
with its wireless mic this measured 130-140 ms, four frames: enough to see.

The measurement follows the inner-lip opening (MediaPipe face landmarks) against the loudness of
the speech: open lips go with loud sound, so the lag where the two line up best is the offset.
On a phone recording (no radio) it reads 0 ms.

MediaPipe is optional (`pip install mediapipe`). It brings its own OpenCV build, so it can live in
a separate Python: point STACKCUT_SYNC_PYTHON at that interpreter and stackcut runs this module
there. This file imports nothing from stackcut and no OpenCV, so it runs in either.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

import numpy as np

MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/"
             "face_landmarker.task")
MODEL_SHA256 = "64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff"
FPS, HEIGHT = 30.0, 480
MIN_R = 0.15          # below this the lips and the sound don't line up clearly enough to judge


class Unavailable(RuntimeError):
    """No MediaPipe here and no STACKCUT_SYNC_PYTHON to run it in."""


def _ffmpeg() -> str:
    return os.environ.get("STACKCUT_FFMPEG") or shutil.which("ffmpeg") or "ffmpeg"


def _model() -> str:
    path = Path.home() / ".cache" / "stackcut" / "face_landmarker.task"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        print("  downloading the MediaPipe face landmarker (3.7 MB, once)", file=sys.stderr)
        urllib.request.urlretrieve(MODEL_URL, path)
    if hashlib.sha256(path.read_bytes()).hexdigest() != MODEL_SHA256:
        path.unlink()
        raise SystemExit("face landmarker checksum mismatch; try again")
    return str(path)


def _signals(video: str, t0: float, t1: float, crop: str | None) -> tuple[np.ndarray, np.ndarray]:
    import mediapipe as mp
    from mediapipe.tasks.python import BaseOptions, vision

    vf = f"fps={FPS}" + (f",crop={crop}" if crop else "") + f",scale=-2:{HEIGHT},format=rgb24"
    base = [_ffmpeg(), "-v", "error", "-ss", f"{t0:.3f}", "-t", f"{t1 - t0:.3f}", "-i", video]
    one = subprocess.run([_ffmpeg(), "-v", "error", "-ss", f"{t0:.3f}", "-i", video, "-map", "0:v:0", "-an", "-vf", vf,
                          "-frames:v", "1", "-f", "rawvideo", "-"], capture_output=True).stdout
    w = len(one) // (HEIGHT * 3)
    raw = subprocess.run(base + ["-map", "0:v:0", "-an", "-vf", vf, "-f", "rawvideo", "-"], capture_output=True).stdout
    frames = np.frombuffer(raw, np.uint8).reshape(-1, HEIGHT, w, 3)
    lm = vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=_model()), running_mode=vision.RunningMode.VIDEO, num_faces=1))
    lips = []
    for i, f in enumerate(frames):
        r = lm.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(f)),
                                int(i * 1000 / FPS))
        if not r.face_landmarks:
            lips.append(np.nan)
            continue
        p, ar = r.face_landmarks[0], HEIGHT / w
        gap = np.hypot(p[13].x - p[14].x, (p[13].y - p[14].y) * ar)      # inner lips, top and bottom
        width = np.hypot(p[78].x - p[308].x, (p[78].y - p[308].y) * ar)  # inner mouth corners
        lips.append(gap / max(width, 1e-6))
    # the voice band only: room noise and a music bed (its mids are scooped) stay out of the envelope
    a = np.frombuffer(subprocess.run(base + ["-vn", "-ac", "1", "-ar", "16000", "-af", "highpass=f=300,lowpass=f=3400",
                                             "-f", "f32le", "-"],
                                     capture_output=True).stdout, np.float32)
    hop = int(16000 / FPS)
    n = min(len(lips), len(a) // hop)
    env = np.log(np.sqrt((a[: n * hop].reshape(n, hop) ** 2).mean(1)) + 1e-4)
    return np.array(lips[:n]), env


def _lag(lips: np.ndarray, env: np.ndarray, max_ms: int = 300, step_ms: int = 5) -> tuple[float, float]:
    t = np.arange(len(lips)) / FPS
    ok = ~np.isnan(lips)
    if ok.sum() < FPS * 3:
        return 0.0, 0.0
    tt = np.arange(0, t[-1], 0.005)
    s, e = np.interp(tt, t[ok], lips[ok]), np.interp(tt, t, env)
    s, e = (s - s.mean()) / (s.std() + 1e-9), (e - e.mean()) / (e.std() + 1e-9)
    best = (0, -1.0)
    for ms in range(-max_ms, max_ms + 1, step_ms):
        k = ms // 5
        c = float(np.mean(s[: len(s) - k] * e[k:]) if k >= 0 else np.mean(s[-k:] * e[: len(e) + k]))
        if c > best[1]:
            best = (ms, c)
    return best[0] / 1000, best[1]


def _measure_here(video: str, t0: float, t1: float, crop: str | None) -> dict:
    lips, env = _signals(video, t0, t1, crop)
    lag, r = _lag(lips, env)
    blk, rng, spread = int(8 * FPS), np.random.default_rng(1), []
    starts = list(range(0, len(env) - blk + 1, blk))
    if len(starts) >= 3:
        for _ in range(40):
            pick = rng.choice(starts, len(starts))
            spread.append(_lag(np.concatenate([lips[i:i + blk] for i in pick]),
                               np.concatenate([env[i:i + blk] for i in pick]), step_ms=10)[0])
    lo, hi = (float(np.percentile(spread, 10)), float(np.percentile(spread, 90))) if spread else (lag, lag)
    return {"lag": lag, "r": r, "lo": lo, "hi": hi, "faces": float(np.mean(~np.isnan(lips)))}


def measure(video: Path | str, t0: float, t1: float, crop: str | None = None, ffmpeg: str | None = None) -> dict:
    """Seconds the sound lags the lips between t0 and t1 (positive: sound late), the correlation r,
    and an 80% range. Runs here if MediaPipe is installed, else in STACKCUT_SYNC_PYTHON."""
    if ffmpeg:
        os.environ["STACKCUT_FFMPEG"] = ffmpeg
    try:
        import mediapipe  # noqa: F401
    except ImportError:
        py = os.environ.get("STACKCUT_SYNC_PYTHON")
        if not py:
            raise Unavailable("lip sync needs MediaPipe: pip install mediapipe, or set STACKCUT_SYNC_PYTHON "
                              "to a Python that has it")
        out = subprocess.run([py, str(Path(__file__).resolve()), str(video), str(t0), str(t1), crop or "-"],
                             capture_output=True, text=True)
        try:
            return json.loads(out.stdout.strip().splitlines()[-1])
        except (IndexError, json.JSONDecodeError):
            raise Unavailable(f"lip sync helper failed: {out.stderr.strip()[-300:]}")
    return _measure_here(str(video), t0, t1, crop)


if __name__ == "__main__":   # run by measure() inside STACKCUT_SYNC_PYTHON
    v, a, b, c = sys.argv[1:5]
    print(json.dumps(_measure_here(v, float(a), float(b), None if c == "-" else c)))
