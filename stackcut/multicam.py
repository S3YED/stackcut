"""layout: multicam. Several cameras filmed the same take; the face take's mic is the one sound.

Each extra angle (a phone filming your back at the desk, a wide shot) is synced to that mic by
its own sound, then checked against the clap at the start of the take: the two must agree within
one frame, or the render stops. Per line the edit picks the view:

  face          the face fills the frame (graphics as in layout: talk)
  split         the screen recording on top in its window, the face below
  <angle>       that angle fills the frame (e.g. `back`: you at work, from behind)
  split:<angle> that angle in the top window instead of the screen, the face below

An angle with `clip: true` is a recording of its own (a page capture, b-roll): it is not synced and
plays from each line's `screen_at` (`continue` picks up where the last line on it stopped).
"""
from __future__ import annotations

import wave
from pathlib import Path

import cv2
import numpy as np

from . import media, screen, speech

W, H = 1080, 1920
FPS = media.FPS
FACE_TOP = 1092
CAPTION_SPLIT, CAPTION_FULL = 1040, 1330
MIN_CONF = 0.3


def _wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768, w.getframerate()


def clap(path: Path, within: float = 60.0, near: float | None = None, radius: float = 0.5) -> float | None:
    """The clap at the start of a take: the sharpest, loudest crack in the first minute (or within
    `radius` seconds of `near`). None when nothing stands out clearly (no clap recorded)."""
    x, sr = _wav(path)
    a = 0 if near is None else max(0, int((near - radius) * sr))
    b = int(within * sr) if near is None else int((near + radius) * sr)
    x = x[a:b]
    hop = int(0.005 * sr)
    n = len(x) // hop
    if n < 10:
        return None
    hp = np.diff(x, prepend=x[0])                      # a first difference: the clap's crack, not speech
    e = np.sqrt((hp[: n * hop].reshape(n, hop) ** 2).mean(1))
    k = int(np.argmax(e))
    burst = e[k:k + 8]
    if e[k] < 15 * (np.median(e) + 1e-6) or (len(burst) == 8 and burst[-1] > 0.5 * e[k]):
        return None                                    # not loud enough over the room, or not short like a clap
    onset = k
    while onset > 0 and e[onset - 1] > 0.3 * e[k]:
        onset -= 1
    return (a / sr) + onset * hop / sr


def blur_mask(w: int, h: int, areas: list[list[float]]) -> np.ndarray | None:
    """A soft-edged mask (0-1) over the areas to hide, [x, y, w, h] as fractions of the frame."""
    if not areas:
        return None
    m = np.zeros((h, w), np.float32)
    for x, y, aw, ah in areas:
        m[int(y * h):int((y + ah) * h), int(x * w):int((x + aw) * w)] = 1
    k = max(3, int(0.04 * max(w, h)) | 1)                  # feathered edge, about 4% of the frame
    return cv2.GaussianBlur(m, (k, k), 0)[:, :, None]


def hide(frame: np.ndarray, mask: np.ndarray, keep_dark: bool = False) -> np.ndarray:
    """Blur the masked areas past reading (a screen, a face, a message), softly into the picture.
    keep_dark: only the lit pixels go soft, so a person sitting in front of a screen stays sharp."""
    h, w = frame.shape[:2]
    if keep_dark:
        luma = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32)
        lit = cv2.GaussianBlur(np.clip((luma - 45) / 45, 0, 1), (0, 0), max(w, h) / 300)
        mask = mask * lit[:, :, None]
    small = cv2.resize(frame, (max(1, w // 16), max(1, h // 16)), interpolation=cv2.INTER_AREA)
    soft = cv2.GaussianBlur(cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR), (0, 0), max(w, h) / 120)
    return (frame * (1 - mask) + soft * mask).astype(np.uint8)


class Angle:
    """One extra camera: its proxy, and its offset to the face take (face time = angle time + offset)."""

    def __init__(self, name: str, spec: dict, work: Path, face_wav: Path):
        self.name, self.crop = name, spec["crop"]
        self.proxy = media.make_proxy(spec["file"], work / f"angle-{name}.mp4")
        self.w, self.h = media.size(self.proxy)
        self.mask = blur_mask(self.w, self.h, spec.get("blur") or [])
        self.keep_dark = bool(spec.get("blur_keep_dark"))
        self.clip = bool(spec.get("clip"))
        if self.clip:
            print(f"· angle {name}: a clip of its own, plays from each line's screen_at")
            return
        wav = media.extract_wav(spec["file"], work / f"angle-{name}16k.wav")
        if spec["offset"] == "auto":
            self.offsets, conf = speech.sync_map(face_wav, wav)
            if conf < MIN_CONF:
                raise SystemExit(f"angle {name}: its sound doesn't line up with the face take (confidence {conf:.2f}). "
                                 "Were both recording at the same time? Set its offset by hand.")
        else:
            self.offsets, conf = speech.Offsets([(0.0, float(spec["offset"]))]), 1.0
        off = self.offsets.at(0)
        cf = clap(face_wav)
        if cf is not None:
            off = self.offsets.at(cf)
        ca = clap(wav, near=cf - off) if cf is not None else None   # the same clap, where the sound match puts it
        msg = f"· angle {name}: face = {name} + {off:.3f} s (sound match {conf:.2f})"
        if cf is None or ca is None:
            msg += ", no clap to check against"
        else:
            by_clap = cf - ca
            msg += f", clap says {by_clap:.3f} s"
            if abs(by_clap - off) > 1 / FPS:
                raise SystemExit(f"sync gate: angle {name} is {abs(by_clap - off) * 1000:.0f} ms off between the sound "
                                 f"match ({off:.3f} s) and the clap ({by_clap:.3f} s), more than one frame. "
                                 "Set its offset by hand after checking the clap frame.")
        if self.offsets.spread > 1 / FPS:
            msg += f"; it shifts by {self.offsets.spread * 1000:.0f} ms along the take, each piece uses its own"
        print(msg)

    def frames(self, face_t: float, n: int, clip_t: float = 0.0):
        """n frames for the piece starting at face_t: a camera follows the face take's time, a clip
        plays from clip_t (the line's screen_at)."""
        t = clip_t if self.clip else face_t - self.offsets.at(face_t)
        for f in media.frames(self.proxy, t, n, self.w, self.h):
            yield f if f is None or self.mask is None else hide(f, self.mask, self.keep_dark)


def fill(frame: np.ndarray, crop: list[float]) -> np.ndarray:
    """The frame scaled to cover 1080x1920, its centre at `crop` (fractions of the frame)."""
    h, w = frame.shape[:2]
    s = max(W / w, H / h)
    vw, vh = W / s, H / s
    x0 = min(max(crop[0] * w - vw / 2, 0), w - vw)
    y0 = min(max(crop[1] * h - vh / 2, 0), h - vh)
    m = np.float32([[s, 0, -x0 * s], [0, s, -y0 * s]])
    return cv2.warpAffine(frame, m, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


class Multi:
    """Draws one frame of the multicam layout, by each line's view."""

    def __init__(self, e, split, talk_draw, angles: dict[str, Angle], look, win_for_angles=None):
        self.e, self.split, self.talk, self.angles, self.look = e, split, talk_draw, angles, look
        self.win = win_for_angles
        self.fade = np.linspace(0.55, 0, 90, dtype=np.float32)[:, None, None]

    def start(self, p, n: int) -> None:
        self.view = self.e.lines[p.line].view
        self.p = p
        self.af = None
        if self.view == "split":
            self.split.start(p, n)
        elif self.view != "face":
            name = self.view.split(":", 1)[-1]
            self.ang = self.angles[name]
            self.af = self.ang.frames(p.a, n, p.screen_in)
            z = self.e.lines[p.line].zoom
            self.zoom = [float(v) for v in z] if isinstance(z, (list, tuple)) else [0.5, 0.5, 1.0]

    def draw(self, k: int, tt: float, ff, cam_at: tuple, fill_: float):
        if self.view == "split":
            return self.split.draw(k, tt, ff, cam_at, fill_)
        if self.view == "face":
            return self.talk(tt, ff, cam_at, fill_)
        af = next(self.af)
        if not self.view.startswith("split:"):
            canvas = fill(af, self.ang.crop) if af is not None else np.zeros((H, W, 3), np.uint8)
            return canvas, CAPTION_FULL, 1.0
        canvas = self.win.bg.copy()
        if af is not None:
            self.win.draw(canvas, screen.view(af, *self.zoom, self.win.w, self.win.h))
        if ff is not None:
            fpanel = self.look.panel(ff, cam_at, W, H - FACE_TOP, fill_).astype(np.float32)
            fpanel[:90] *= 1 - self.fade
            canvas[FACE_TOP:] = fpanel.astype(np.uint8)
        return canvas, CAPTION_SPLIT, 1.0
