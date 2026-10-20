"""The edit file (edit.yaml): what plays, in which order, and what the screen shows meanwhile.

It is written by `stackcut draft` (automatic) or by you / your AI agent, then rendered as is."""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_STYLE = {
    "accent": "#0088ff",                                  # caption keyword pill and progress bar
    "background": ["#0088ff", "#6d5dfc", "#143cbe"],      # the three soft glows behind the window
    "base": "#0a1024",                                    # the dark wallpaper under them
    "top_crop": 0.0,                                      # hide this fraction of the screen's top (browser chrome)
    "face_fill": 0.36,                                    # face height as a share of the face panel
    "white_balance": 0.5,                                 # 0 = off, 1 = fully neutral (white shirt reference)
    "caption_words": 3,
    "caption_chars": 16,
    "music_lufs": -32,                                    # the bed in the pauses, under a -14 LUFS voice
    "music_start": 0.0,                                   # skip a track's quiet intro (seconds)
    "music_duck_db": 4,                                   # how far the bed dips while you talk
    "music_mid_cut_db": 4,                                # 1-4 kHz scooped out of the bed: it never sits on the voice
    "sfx": True,                                          # soft impact on the first frame, pops on the hook and the end
    "tightness": "normal",                                # "tight": about 0.08 s between words at every cut
    "punch": False,                                       # face zooms in a little on key words (keeps a tired take moving)
    "face_grade": {"brightness": 0.0, "warmth": 0.0, "eyes": 0.0},   # e.g. 0.06 / 0.04 / 0.08 for a dim room
    "speed": 1.0,                                         # 1.05-1.1 lifts a slow delivery; pitch stays the same
    "voice": "denoise",                                   # "denoise": room noise out, voice untouched (DeepFilterNet);
                                                          # "light": the mic as is; "clean": FFmpeg denoise for a poor mic;
                                                          # "isolate": ElevenLabs Voice Isolator (ELEVENLABS_API_KEY)
    "denoise_db": 24,                                     # most noise "denoise" may remove; lower keeps more room air
}
VOICES = ("denoise", "light", "clean", "isolate")


@dataclass
class Line:
    start: float                      # face-take seconds
    end: float
    zoom: object = "auto"             # "full", "auto" or [cx, cy, z]
    screen_at: float | None = None    # show this screen moment instead of the synced one
    join: bool = False                # the sentence continues from the line before
    tight: bool = True                # cut pauses and breaths inside the line
    pad_in: float | None = None       # extra lead-in for a soft first word ("I", "If")
    cut: bool = False                 # jump to the zoom instead of easing
    beat: float = 0.0                 # hold this much of the take's own pause after the line (after a hook)


@dataclass
class Edit:
    root: Path
    screen: Path
    face: Path
    lines: list[Line]
    hook: str = ""
    offset: object = "auto"
    music: Path | None = None
    privacy: list[dict] = field(default_factory=list)
    style: dict = field(default_factory=lambda: dict(DEFAULT_STYLE))
    fix: dict = field(default_factory=dict)


def load(path: Path) -> Edit:
    d = yaml.safe_load(path.read_text())
    root = path.resolve().parent
    lines = []
    for i, l in enumerate(d.get("lines") or []):
        if "from" not in l or "to" not in l:
            raise SystemExit(f"line {i + 1} in {path.name} needs 'from' and 'to' (face-take seconds)")
        lines.append(Line(float(l["from"]), float(l["to"]), l.get("zoom", "auto"), l.get("screen_at"),
                          bool(l.get("join", False)), bool(l.get("tight", True)), l.get("pad_in"),
                          bool(l.get("cut", False)), float(l.get("beat", 0.0))))
    if not lines:
        raise SystemExit(f"{path.name} has no lines")
    style = dict(DEFAULT_STYLE)
    style.update(d.get("style") or {})
    style["face_grade"] = {**DEFAULT_STYLE["face_grade"], **(style.get("face_grade") or {})}
    if not 0.8 <= float(style["speed"]) <= 1.25:
        raise SystemExit(f"{path.name}: style.speed {style['speed']} is outside 0.8-1.25")
    if style["voice"] not in VOICES:
        raise SystemExit(f"{path.name}: style.voice must be one of {', '.join(VOICES)}")
    music = d.get("music")
    for key in ("screen", "face") + (("music",) if music else ()):
        if not (root / d[key]).exists():
            raise SystemExit(f"{path.name}: {key} file not found: {root / d[key]}")
    return Edit(root, (root / d["screen"]).resolve(), (root / d["face"]).resolve(), lines, d.get("hook") or "",
                d.get("offset", "auto"), (root / music).resolve() if music else None, d.get("privacy") or [], style,
                {str(k).lower(): str(v) for k, v in (d.get("fix") or {}).items()})


def _similar(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower().split(), b.lower().split()).ratio()


def draft(screen: str, face: str, sents: list[dict], offset: float, screen_dur: float, out: Path,
          speech_runs: list[tuple[float, float]] | None = None) -> Path:
    """An automatic first edit: every sentence once (for a line said twice, the last take wins),
    the screen synced, zoom 'auto'. Good enough to post; better after an AI or human pass.
    Sentences the voice detector barely hears (whisper's "Thank you." on room noise) are dropped."""
    def voiced(s: dict) -> float:
        span = max(0.01, s["end"] - s["start"])
        return sum(max(0.0, min(b, s["end"]) - max(a, s["start"])) for a, b in speech_runs or []) / span
    if speech_runs:
        sents = [s for s in sents if voiced(s) >= 0.4]
    keep = []
    for i, s in enumerate(sents):
        nxt = sents[i + 1] if i + 1 < len(sents) else None
        if nxt and _similar(s["text"], nxt["text"]) > 0.55:
            continue      # a retake follows: keep the later one
        keep.append(s)
    rows = []
    for s in keep:
        a, b = round(s["start"], 2), round(s["end"] + 0.05, 2)
        st = a - offset
        extra = "" if 0 <= st <= screen_dur - 0.5 else f", screen_at: {max(0.0, min(screen_dur - 3, st)):.2f}"
        text = s["text"].replace('"', "'")
        rows.append(f"  - {{from: {a:.2f}, to: {b:.2f}, zoom: auto{extra}}}   # \"{text}\"")
    hook = ""
    out.write_text(f"""# stackcut edit file. Times are seconds in the FACE take.
# zoom: full | auto | [cx, cy, z]   (cx, cy = centre as fractions of the screen, z >= 1)
# screen_at: show this screen moment instead of the synced one.  join: the sentence continues.
# beat: seconds of pause held after a line (one short beat after the hook lands).
screen: {screen}
face: {face}
offset: {offset:.2f}          # face time = screen time + offset (measured from the audio)
hook: "{hook}"                # one line on top, about 28 characters. Empty = no sticker.
music: null                   # optional: a music file for a quiet bed under the voice
style:
  top_crop: 0.0               # e.g. 0.1 hides a browser's tabs and address bar in full view
privacy: []                   # blur boxes on the screen: - {{rect: [x, y, w, h], from: 0, to: 60}}
fix: {{}}                       # caption corrections, word for word: {{stream: screen}}
lines:
""" + "\n".join(rows) + "\n")
    return out
