"""Everything about the voice: transcription, sync between two recordings, and silence cuts."""
from __future__ import annotations

import json
import re
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

SR = 16000


@dataclass
class Word:
    text: str
    start: float
    end: float


def load_wav(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768.0


# ---------------------------------------------------------------- transcription

PROMPTS = {   # a short punctuated sentence in the take's own language, for the second pass
    "en": "Hi, I'm recording a short video. Here's what I built, and why.",
    "nl": "Hoi, ik neem een korte video op. Dit heb ik gebouwd, en waarom.",
    "de": "Hallo, ich nehme ein kurzes Video auf. Das habe ich gebaut, und warum.",
    "fr": "Salut, j'enregistre une courte vidéo. Voici ce que j'ai construit, et pourquoi.",
    "es": "Hola, estoy grabando un video corto. Esto es lo que construí, y por qué.",
}


def _punctuated(words: list[dict]) -> bool:
    return sum(bool(re.search(r"[.?!]$", w["w"])) for w in words) >= max(1, len(words) // 40)


def transcribe(wav: Path, out: Path, model: str = "large-v3-turbo", language: str | None = None) -> dict:
    """Word-level transcript with faster-whisper, cached as JSON next to the project.
    Whisper sometimes returns a take in lower case without punctuation; then it runs once more with a
    punctuated prompt in the take's own language (a prompt in another language makes it skip speech)."""
    if out.exists():
        return json.loads(out.read_text())
    from faster_whisper import WhisperModel
    print(f"  transcribing {wav.name} with whisper {model} (first run downloads the model)")
    m = WhisperModel(model, device="auto", compute_type="int8")
    audio = load_wav(wav)   # samples, not a path: faster-whisper's own decoder depends on a drifting PyAV

    def run(lang, prompt=None, a=0.0, b=None):
        clip = audio[int(a * SR):None if b is None else int(b * SR)]
        segs, info = m.transcribe(clip, word_timestamps=True, beam_size=5, language=lang, vad_filter=False,
                                  condition_on_previous_text=prompt is not None, initial_prompt=prompt)
        return [{"w": w.word.strip(), "s": round(a + w.start, 3), "e": round(a + w.end, 3)}
                for s in segs for w in (s.words or []) if w.word.strip()], info.language

    words, lang = run(language)
    if not _punctuated(words) and lang in PROMPTS:
        print("  no punctuation came back; transcribing once more with a punctuated prompt")
        again, _ = run(lang, PROMPTS[lang])
        if _punctuated(again) and len(again) >= 0.8 * len(words):
            words = again
    words = fill_gaps(words, wav, lambda a, b: run(lang, PROMPTS.get(lang), a, b)[0])
    data = {"language": lang, "words": tidy(words, wav)}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=0))
    return data


STRETCHED = 1.0   # a "word" longer than this is whisper stretching it over a pause or skipped speech


def fill_gaps(words: list[dict], wav: Path, hear) -> list[dict]:
    """Whisper tends to skip a retake (it hears a repeat and drops it) and stretches the word before
    it over the skipped speech. So: clip stretched words to their own stretch of speech, then hear
    every stretch of speech that still has no words once more on its own (`hear(a, b)` returns the
    words between a and b), so every take is there to pick from."""
    runs = speech_runs(wav)
    out = []
    for w in words:
        if w["e"] - w["s"] > STRETCHED:
            own = [b for a, b in runs if a <= w["s"] + 0.2 and b > w["s"]]
            if own and own[-1] + 0.05 < w["e"] - 0.3:
                w = {**w, "e": round(own[-1] + 0.05, 3)}
        out.append(w)
    spans = sorted((w["s"], w["e"]) for w in out)
    holes: list[list[float]] = []
    for a, b in runs:
        cur = a
        for s, e in spans:
            if e <= cur or s >= b:
                continue
            if s > cur:
                holes.append([cur, s])
            cur = max(cur, e)
        if b > cur:
            holes.append([cur, b])
    merged: list[list[float]] = []
    for h in holes:
        if merged and h[0] - merged[-1][1] < 0.35:
            merged[-1][1] = h[1]
        else:
            merged.append(h)
    for h0, h1 in (h for h in merged if h[1] - h[0] >= 0.5):
        new = []
        for w in hear(max(0.0, h0 - 1.0), h1 + 1.0):
            mid = (w["s"] + w["e"]) / 2
            if h0 - 0.1 <= mid <= h1 + 0.1 and not any(s <= mid <= e for s, e in spans):
                new.append(w)
        if new:
            print(f"  heard {len(new)} more words at {h0:.1f} s (a retake whisper skipped)")
            out += new
    return sorted(out, key=lambda w: w["s"])


def tidy(words: list[dict], wav: Path | None = None) -> list[dict]:
    """Whisper sometimes returns a take all in lower case without punctuation. Then: capital 'I',
    and a sentence break wherever the voice pauses for 0.3 s or more (measured with the VAD, because
    whisper stretches words across pauses)."""
    if not words or _punctuated(words):
        return words
    gaps = []
    if wav is not None:
        runs = speech_runs(wav)
        gaps = [(a[1], b[0]) for a, b in zip(runs, runs[1:]) if b[0] - a[1] >= 0.3]
    fixes = {"i": "I", "i'm": "I'm", "i've": "I've", "i'll": "I'll", "i'd": "I'd", "ai": "AI"}
    out, new_sentence = [], True
    for i, w in enumerate(words):
        t = fixes.get(w["w"].lower(), w["w"])
        if new_sentence:
            t = t[:1].upper() + t[1:]
        nxt = words[i + 1] if i + 1 < len(words) else None
        mid = (w["s"] + w["e"]) / 2
        new_sentence = nxt is None or nxt["s"] - w["e"] > 0.45 or any(
            mid < (g0 + g1) / 2 < (nxt["s"] + nxt["e"]) / 2 for g0, g1 in gaps)
        if new_sentence and not re.search(r"[.?!,]$", t):
            t += "."
        out.append({**w, "w": t})
    return out


def sentences(words: list[dict]) -> list[dict]:
    """Split a transcript into sentences with their times: what an editor (human or AI) picks from."""
    out, cur = [], []
    for w in words:
        cur.append(w)
        if re.search(r"[.?!]$", w["w"]):
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return [{"i": i, "start": s[0]["s"], "end": s[-1]["e"], "text": " ".join(w["w"] for w in s)}
            for i, s in enumerate(out)]


# ---------------------------------------------------------------- sync

def sync_offset(face_wav: Path, screen_wav: Path) -> tuple[float, float]:
    """Seconds to add to a screen-recording time to get the face-take time, and how sure we are (0-1).
    Cross-correlates the loudness envelopes (10 ms steps) of both microphones."""
    def env(p: Path) -> np.ndarray:
        a = load_wav(p)
        a = a[: len(a) // 160 * 160].reshape(-1, 160)
        e = np.log1p(np.sqrt((a ** 2).mean(1)) * 32768)
        return (e - e.mean()) / (e.std() + 1e-9)
    a, b = env(face_wav), env(screen_wav)
    n = 1 << int(np.ceil(np.log2(len(a) + len(b))))
    c = np.fft.irfft(np.fft.rfft(a, n) * np.conj(np.fft.rfft(b, n)), n)
    lag = int(np.argmax(c))
    lag = lag - n if lag > n // 2 else lag
    return lag / 100.0, float(c.max() / min(len(a), len(b)))


class Offsets:
    """face time = other time + offset, where the offset may change along the take: a screen recorder
    that drops half a second, a camera that drifts. Piecewise from 30 s windows."""

    def __init__(self, points: list[tuple[float, float]]):
        self.points = sorted(points)

    def at(self, t: float) -> float:
        return min(self.points, key=lambda p: abs(p[0] - t))[1]

    @property
    def spread(self) -> float:
        offs = [o for _, o in self.points]
        return max(offs) - min(offs)


def sync_map(face_wav: Path, other_wav: Path, win: float = 30.0, step: float = 10.0,
             min_conf: float = 0.4) -> tuple[Offsets, float]:
    """The offset per stretch of the take: each 30 s window of the face take is cross-correlated
    against the whole other track. Windows that match weakly are dropped; a lone window that
    disagrees with both neighbours is treated as noise. Returns the map and the whole-take match."""
    def env(p: Path) -> np.ndarray:
        a = load_wav(p)
        a = a[: len(a) // 160 * 160].reshape(-1, 160)
        return np.log1p(np.sqrt((a ** 2).mean(1)) * 32768)
    f, o = env(face_wav), env(other_wav)
    whole, conf = sync_offset(face_wav, other_wav)
    O = (o - o.mean()) / (o.std() + 1e-9)
    pts = []
    for t in np.arange(0, max(1.0, len(f) / 100 - win / 2), step):
        seg = f[int(t * 100):int((t + win) * 100)]
        if len(seg) < 500 or seg.std() < 1e-6:
            continue
        seg = (seg - seg.mean()) / seg.std()
        n = 1 << int(np.ceil(np.log2(len(O) + len(seg))))
        c = np.fft.irfft(np.fft.rfft(O, n) * np.conj(np.fft.rfft(seg, n)), n)[: len(O)]
        k = int(np.argmax(c))
        if c[k] / len(seg) >= min_conf:
            pts.append((float(t + win / 2), round(float(t - k / 100.0), 3)))
    def off(i: int, j: int) -> bool:
        return abs(pts[i][1] - pts[j][1]) > 0.05

    def lone(i: int) -> bool:   # disagrees with its neighbours: noise, or a window the other track hadn't started for
        if 0 < i < len(pts) - 1:
            return off(i, i - 1) and off(i, i + 1)
        if len(pts) < 3:
            return False
        a, b = (1, 2) if i == 0 else (i - 1, i - 2)
        return off(i, a) and not off(a, b)
    for _ in range(3):                  # two bad windows in a row at the start: drop them in turn
        clean = [p for i, p in enumerate(pts) if not lone(i)]
        if len(clean) == len(pts):
            break
        pts = clean
    return Offsets(pts or [(0.0, whole)]), conf


# ---------------------------------------------------------------- silence cuts

PAD_IN, PAD_OUT, MERGE_GAP, MIN_RUN = 0.07, 0.05, 0.11, 0.14
_vad_cache: dict[str, list[tuple[float, float]]] = {}


def speech_runs(wav: Path) -> list[tuple[float, float]]:
    key = str(wav)
    if key not in _vad_cache:
        from faster_whisper.vad import VadOptions, get_speech_timestamps
        opts = VadOptions(threshold=0.5, min_silence_duration_ms=90, speech_pad_ms=0, min_speech_duration_ms=60)
        _vad_cache[key] = [(t["start"] / SR, t["end"] / SR) for t in get_speech_timestamps(load_wav(wav), opts)]
    return _vad_cache[key]


LEVEL_HOP = 0.02
_level_cache: dict[str, tuple[np.ndarray, float]] = {}


def _voiced(wav: Path, t: float) -> bool:
    """Is the 20 ms at t louder than the take's room tone (its 10th-percentile level) by 4 dB or more?"""
    key = str(wav)
    if key not in _level_cache:
        x, n = load_wav(wav), int(LEVEL_HOP * SR)
        frames = x[: len(x) // n * n].reshape(-1, n)
        db = 20 * np.log10(np.sqrt((frames ** 2).mean(1)) + 1e-9)
        _level_cache[key] = (db, float(np.percentile(db, 10)))
    db, floor = _level_cache[key]
    i = int(round(t / LEVEL_HOP, 6))
    return 0 <= i < len(db) and db[i] > floor + 4


TIGHTNESS = {   # (air before a run, air after a run, pauses shorter than this stay inside a run)
    "normal": (PAD_IN, PAD_OUT, MERGE_GAP),
    "tight": (0.05, 0.03, 0.06),     # about 0.08 s between words at every cut: no dead air at all
}


def tighten(wav: Path, words: list[dict], a: float, b: float, pad_in: float | None = None,
            tightness: str = "normal") -> list[tuple[float, float]]:
    """Keep only the voiced parts of [a, b]: every pause and breath becomes a jump cut.
    Never clips a word: a run that just misses a word edge is stretched over it, and a soft word the
    voice detector missed in a noisy room ("een", "Wat") but whisper heard right next to a run is kept.
    pad_in: extra lead-in before the line's first run only."""
    t_in, pad_out, merge_gap = TIGHTNESS.get(tightness, TIGHTNESS["normal"])
    first_in = t_in if pad_in is None else pad_in
    ws = [(w["s"], w["e"]) for w in words if w["e"] > a and w["s"] < b]
    voiced = [(s, e) for s, e in speech_runs(wav) if e > a and s < b]
    runs = [(max(a, s - (first_in if k == 0 else t_in)), min(b, e + pad_out)) for k, (s, e) in enumerate(voiced)]
    def heard(ws_: float, we: float) -> float:   # share of a word the voice detector heard
        return sum(max(0.0, min(we, ve) - max(ws_, vs)) for vs, ve in voiced) / max(we - ws_, 1e-3)

    soft = [(ws_, we) for ws_, we in ws if heard(ws_, we) < 0.5]
    fixed = []
    for s, e in runs:
        for ws_, we in ws:
            if ws_ < e and we > s:
                if 0 < s - ws_ < 0.15:
                    s = max(a, ws_ - 0.02)
                if 0 < we - e < 0.15:
                    e = min(b, we + 0.02)
        for ws_, we in soft:     # whisper heard it, the voice detector didn't: follow the voice's energy into it
            if ws_ < s < we + 0.15:
                lo = max(a, ws_ - 0.02, s - 0.25)
                while s - LEVEL_HOP >= lo and _voiced(wav, s - LEVEL_HOP):
                    s -= LEVEL_HOP
            if ws_ - 0.15 < e < we:
                hi = min(b, we + 0.02, e + 0.25)
                while e + LEVEL_HOP <= hi and _voiced(wav, e):
                    e += LEVEL_HOP
        fixed.append((s, e))
    merged: list[list[float]] = []
    for s, e in sorted(fixed):
        if merged and s - merged[-1][1] < merge_gap:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [(round(s, 3), round(e, 3)) for s, e in merged
            if any(s <= (ws_ + we) / 2 <= e for ws_, we in ws)] or [(a, b)]


EDGE_HOP, EDGE_QUIET, EDGE_VOICE = 0.01, 8.0, 14.0   # 10 ms steps; dB over the room tone
_edge_cache: dict[str, tuple[np.ndarray, float]] = {}


def _edges_env(wav: Path) -> tuple[np.ndarray, float]:
    key = str(wav)
    if key not in _edge_cache:
        x, n = load_wav(wav), int(EDGE_HOP * SR)
        db = 20 * np.log10(np.sqrt((x[: len(x) // n * n].reshape(-1, n) ** 2).mean(1)) + 1e-9)
        _edge_cache[key] = (db, float(np.percentile(db, 10)))
    return _edge_cache[key]


def settle_edges(wav: Path, words: list[dict], a: float, b: float, keep_a: bool = False,
                 keep_b: bool = False) -> tuple[float, float, str, bool]:
    """Move a piece's ends onto the real edges of its words, measured in the sound.

    A line's times come from whisper, whose word edges are often 100 ms or more off: a cut there stops
    the last word while it still sounds (the caption shows it, the ear hears half of it). The end moves
    on until the voice has died away; when the next word follows without any pause, the cut goes in
    the dip the next word rises out of. Whisper's times only bound the search (to about the middle of
    the next word; a word it stretched over a pause, longer than 0.8 s, bounds nothing). The start does
    the same backwards, but only into a voiced sound, never into a breath.
    Returns (a, b, what moved, whether the end is a joined-speech cut that needs a softer fade)."""
    db, floor = _edges_env(wav)

    def lv(t: float) -> float:
        return float(db[max(0, min(len(db) - 1, int(round(t / EDGE_HOP))))])

    real = [w for w in words if w["e"] - w["s"] < 0.8]

    def walk(t: float, step: float, limit: float, near: float | None) -> tuple[float | None, bool]:
        """From t outwards up to limit: the first quiet 10 ms (the word has died away), or the bottom
        of the dip where the next word rises out of it (joined speech; only near where whisper heard
        that word, so a "t" inside "websites" is not taken for it). None when neither comes."""
        start, m, mi, u = lv(t), lv(t), t, t
        while (u < limit) if step > 0 else (u > limit):
            v = lv(u if step > 0 else u - EDGE_HOP)
            if v < floor + EDGE_QUIET:
                return u, False
            if v < m:
                m, mi = v, u
            elif v > m + 6 and m < start - 4 and near is not None and abs(u - near) < 0.15:
                return mi, True
            u += step
        return None, True

    note, joined = [], False
    if not keep_b and lv(b - 0.015) >= floor + EDGE_QUIET:            # still sounding at the cut
        nxt = next((w for w in real if (w["s"] + w["e"]) / 2 > b + 0.02), None)
        limit = min(b + 0.40, max((nxt["s"] + nxt["e"]) / 2, nxt["s"] + 0.15)) if nxt else b + 0.40
        u, joined = walk(b, EDGE_HOP, limit, nxt["s"] if nxt else None)
        if u is not None:
            nb = u + (0.02 if not joined else 0.0)
        else:                                                         # no dip at all: a vowel runs into a vowel
            nb = min(max(b, nxt["s"] + 0.01), limit) if nxt else limit
        if abs(nb - b) >= 0.01:
            note.append(f"end {'+' if nb > b else ''}{(nb - b) * 1000:.0f} ms" + (" (joined speech)" if joined else ""))
        b = nb
    if not keep_a and lv(a - 0.02) >= floor + EDGE_VOICE:             # cutting into a voiced sound
        prv = next((w for w in reversed(real) if (w["s"] + w["e"]) / 2 < a - 0.02), None)
        limit = max(a - 0.40, min((prv["s"] + prv["e"]) / 2, prv["e"] - 0.15)) if prv else a - 0.40
        u, j = walk(a, -EDGE_HOP, limit, prv["e"] if prv else None)
        if u is not None:
            na = u - (0.02 if not j else 0.0)
        else:
            na = max(min(a, prv["e"] - 0.01), limit) if prv else limit
        if abs(na - a) >= 0.01:
            note.append(f"start {'+' if na > a else ''}{(na - a) * 1000:.0f} ms")
        a = na
    return round(a, 3), round(b, 3), ", ".join(note), joined


def words_in(words: list[dict], a: float, b: float) -> list[dict]:
    """Words spoken inside [a, b]. Whisper stretches a phrase's first word back into the silence
    before it, so a word that starts before the cut still counts when a bit of it is after the cut."""
    out = []
    for w in words:
        overlap = min(w["e"], b) - max(w["s"], a)
        lead_in = w["s"] < a and overlap >= 0.04
        if overlap >= min(0.12, 0.5 * (w["e"] - w["s"])) or lead_in:
            out.append(w)
    return out


# ---------------------------------------------------------------- take energy

def _pitch(frame: np.ndarray) -> float | None:
    """Fundamental frequency (Hz) of one 40 ms frame by autocorrelation, or None when unvoiced."""
    f = frame - frame.mean()
    if np.sqrt((f ** 2).mean()) < 0.01:
        return None
    ac = np.correlate(f, f, "full")[len(f) - 1:]
    lo, hi = SR // 400, SR // 75
    lag = lo + int(np.argmax(ac[lo:hi]))
    return SR / lag if ac[lag] > 0.3 * ac[0] else None


def energy(wav: Path, words: list[dict], a: float, b: float) -> dict:
    """How much conviction a take carries: loudness of the voiced part, pitch movement, pace and
    the longest pause. Louder, more melody, steady pace and no long pauses read as awake."""
    audio = load_wav(wav)
    seg = audio[int(a * SR):int(b * SR)]
    frames = seg[: len(seg) // 640 * 640].reshape(-1, 640)
    if not len(frames):        # shorter than one 40 ms frame: nothing to measure
        return {"loud_db": -60.0, "pitch_range": 0.0, "words_per_s": 0.0, "longest_pause": 0.0}
    rms = np.sqrt((frames ** 2).mean(1))
    voiced = rms > max(0.01, np.percentile(rms, 30))
    loud = 20 * np.log10(rms[voiced].mean() + 1e-9) if voiced.any() else -60.0
    f0 = [p for fr, v in zip(frames, voiced) if v for p in [_pitch(fr)] if p]
    semis = 12 * np.log2(np.array(f0) / np.median(f0)) if len(f0) > 5 else np.array([0.0])
    ws = [w for w in words if w["s"] >= a - 0.05 and w["e"] <= b + 0.05]
    dur = max(0.1, b - a)
    gaps = [n["s"] - w["e"] for w, n in zip(ws, ws[1:])]
    return {"loud_db": round(float(loud), 1), "pitch_range": round(float(np.percentile(semis, 90) - np.percentile(semis, 10)), 1),
            "words_per_s": round(len(ws) / dur, 2), "longest_pause": round(max(gaps, default=0.0), 2)}
