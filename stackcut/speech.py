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

    def run(lang, prompt=None):
        segs, info = m.transcribe(audio, word_timestamps=True, beam_size=5, language=lang, vad_filter=False,
                                  condition_on_previous_text=prompt is not None, initial_prompt=prompt)
        return [{"w": w.word.strip(), "s": round(w.start, 3), "e": round(w.end, 3)}
                for s in segs for w in (s.words or []) if w.word.strip()], info.language

    words, lang = run(language)
    if not _punctuated(words) and lang in PROMPTS:
        print("  no punctuation came back; transcribing once more with a punctuated prompt")
        again, _ = run(lang, PROMPTS[lang])
        if _punctuated(again) and len(again) >= 0.8 * len(words):
            words = again
    data = {"language": lang, "words": tidy(words, wav)}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=0))
    return data


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


def tighten(wav: Path, words: list[dict], a: float, b: float, pad_in: float = PAD_IN) -> list[tuple[float, float]]:
    """Keep only the voiced parts of [a, b]: every pause and breath becomes a jump cut.
    Never clips a word: a run that just misses a word edge is stretched over it."""
    ws = [(w["s"], w["e"]) for w in words if w["e"] > a and w["s"] < b]
    runs = [(max(a, s - pad_in), min(b, e + PAD_OUT)) for s, e in speech_runs(wav) if e > a and s < b]
    fixed = []
    for s, e in runs:
        for ws_, we in ws:
            if ws_ < e and we > s:
                if 0 < s - ws_ < 0.15:
                    s = max(a, ws_ - 0.02)
                if 0 < we - e < 0.15:
                    e = min(b, we + 0.02)
        fixed.append((s, e))
    merged: list[list[float]] = []
    for s, e in sorted(fixed):
        if merged and s - merged[-1][1] < MERGE_GAP:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [(round(s, 3), round(e, 3)) for s, e in merged
            if any(s <= (ws_ + we) / 2 <= e for ws_, we in ws)] or [(a, b)]


def words_in(words: list[dict], a: float, b: float) -> list[dict]:
    """Words spoken inside [a, b]. Whisper stretches a phrase's first word back into the silence
    before it, so a word that starts before the cut still counts when a bit of it is after the cut."""
    out = []
    for w in words:
        overlap = min(w["e"], b) - max(w["s"], a)
        lead_in = w["s"] < a and overlap >= 0.06
        if overlap >= min(0.12, 0.5 * (w["e"] - w["s"])) or lead_in:
            out.append(w)
    return out
