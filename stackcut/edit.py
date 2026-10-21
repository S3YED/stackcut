"""The edit file (edit.yaml): what plays, in which order, and what the picture shows meanwhile.

Three layouts: `split` (a screen recording on top, the face below), `talk` (the face fills the
frame; animated graphics per line) and `multicam` (several cameras synced to the face take's mic;
each line picks its view). Written by `stackcut draft` (automatic) or by you / your AI agent, then
rendered as is."""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import graphics

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
LAYOUTS = ("split", "talk", "multicam")


@dataclass
class Line:
    start: float                      # face-take seconds
    end: float
    zoom: object = "auto"             # split: "full", "auto" or [cx, cy, z]
    screen_at: float | None = None    # split: show this screen moment instead of the synced one
    join: bool = False                # the sentence continues from the line before
    tight: bool = True                # cut pauses and breaths inside the line
    pad_in: float | None = None       # extra lead-in for a soft first word ("I", "If")
    cut: bool = False                 # jump to the zoom instead of easing
    beat: float = 0.0                 # hold this much of the take's own pause after the line (after a hook)
    graphic: tuple | None = None      # talk: (kind, spec, place) from graphics.parse
    face_zoom: float = 1.0            # the face this much closer for the line: a zoom cut
    view: str = ""                    # multicam: split | face | <angle> | split:<angle>


@dataclass
class Edit:
    root: Path
    screen: Path | None
    face: Path
    lines: list[Line]
    layout: str = "split"
    hook: str = ""
    offset: object = "auto"
    av_offset: object = "auto"        # seconds the face take's sound lags its own picture (a wireless mic)
    music: Path | None = None
    privacy: list[dict] = field(default_factory=list)
    style: dict = field(default_factory=lambda: dict(DEFAULT_STYLE))
    fix: dict = field(default_factory=dict)
    angles: dict = field(default_factory=dict)   # multicam: name -> {"file", "offset", "crop": [x, y], "blur": [[x, y, w, h]]}
    language: str | None = None       # the take's language for transcription (auto when unset)


def _views(angles: dict, screen: bool) -> set[str]:
    return ({"face"} | ({"split"} if screen else set()) | set(angles) | {f"split:{a}" for a in angles})


def load(path: Path) -> Edit:
    d = yaml.safe_load(path.read_text())
    root = path.resolve().parent
    layout = d.get("layout") or ("split" if d.get("screen") else "talk")
    if layout not in LAYOUTS:
        raise SystemExit(f"{path.name}: layout must be one of {', '.join(LAYOUTS)}")
    angles = {}
    for name, a in (d.get("angles") or {}).items() if layout == "multicam" else ():
        a = {"file": a} if isinstance(a, str) else dict(a)
        if not a.get("file") or not (root / a["file"]).exists():
            raise SystemExit(f"{path.name}: angle {name} file not found: {root / str(a.get('file'))}")
        off = a.get("offset", "auto")
        if off != "auto":
            off = float(off)
        crop = [float(v) for v in a.get("crop", [0.5, 0.5])]
        blur = [[float(v) for v in r] for r in a.get("blur") or []]
        if any(len(r) != 4 or not all(0 <= v <= 1 for v in r) for r in blur):
            raise SystemExit(f"{path.name}: angle {name} blur: each area is [x, y, w, h] as fractions of the frame (0-1)")
        angles[str(name)] = {"file": (root / a["file"]).resolve(), "offset": off, "crop": crop, "blur": blur,
                          "blur_keep_dark": bool(a.get("blur_keep_dark"))}
    views = _views(angles, bool(d.get("screen")))
    lines = []
    for i, l in enumerate(d.get("lines") or []):
        where = f"{path.name} line {i + 1}"
        view = ""
        if layout == "multicam":
            view = str(l.get("view") or ("split" if d.get("screen") else "face"))
            if view not in views:
                raise SystemExit(f"{where}: view {view} is not one of {', '.join(sorted(views))}")
        if "from" not in l or "to" not in l:
            raise SystemExit(f"{where} needs 'from' and 'to' (face-take seconds)")
        if (layout == "talk" or (layout == "multicam" and view == "face")) and ("zoom" in l or "screen_at" in l):
            raise SystemExit(f"{where}: zoom and screen_at are for the screen (layout: split); "
                             "in layout: talk the face zooms with face_zoom")
        if l.get("graphic") is not None and not (layout == "talk" or view == "face"):
            raise SystemExit(f"{where}: graphic works in layout: talk, or on a multicam line with view: face")
        fz = float(l.get("face_zoom", 1.0))
        if not 0.8 <= fz <= 1.6:
            raise SystemExit(f"{where}: face_zoom {fz} is outside 0.8-1.6")
        lines.append(Line(float(l["from"]), float(l["to"]), l.get("zoom", "auto"), l.get("screen_at"),
                          bool(l.get("join", False)), bool(l.get("tight", True)), l.get("pad_in"),
                          bool(l.get("cut", False)), float(l.get("beat", 0.0)),
                          graphics.parse(l["graphic"], root, where) if l.get("graphic") is not None else None, fz,
                          view))
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
    needs_screen = layout == "split" or any(ln.view == "split" for ln in lines)
    for key in ("face",) + (("screen",) if needs_screen else ()) + (("music",) if music else ()):
        if not d.get(key):
            raise SystemExit(f"{path.name}: needs '{key}:'")
        if not (root / d[key]).exists():
            raise SystemExit(f"{path.name}: {key} file not found: {root / d[key]}")
    av = d.get("av_offset", "auto")
    if av != "auto" and not -1.0 <= float(av) <= 1.0:
        raise SystemExit(f"{path.name}: av_offset {av} is outside -1..1 s")
    screen = (root / d["screen"]).resolve() if layout in ("split", "multicam") and d.get("screen") else None
    return Edit(root, screen, (root / d["face"]).resolve(), lines, layout, d.get("hook") or "",
                d.get("offset", "auto"), av, (root / music).resolve() if music else None, d.get("privacy") or [], style,
                {str(k).lower(): str(v) for k, v in (d.get("fix") or {}).items()}, angles, d.get("language"))


def _similar(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower().split(), b.lower().split()).ratio()


def draft(face: str, sents: list[dict], out: Path, speech_runs: list[tuple[float, float]] | None = None,
          screen: str | None = None, offset: float = 0.0, screen_dur: float = 0.0) -> Path:
    """An automatic first edit: every sentence once (for a line said twice, the last take wins).
    With a screen recording: the split layout, the screen synced, zoom 'auto'. Without one: the
    talking-head layout, no graphics yet (that is the editor's pass).
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
        text = s["text"].replace('"', "'")
        if screen is None:
            rows.append(f"  - {{from: {a:.2f}, to: {b:.2f}}}   # \"{text}\"")
            continue
        st = a - offset
        extra = "" if 0 <= st <= screen_dur - 0.5 else f", screen_at: {max(0.0, min(screen_dur - 3, st)):.2f}"
        rows.append(f"  - {{from: {a:.2f}, to: {b:.2f}, zoom: auto{extra}}}   # \"{text}\"")
    if screen is None:
        head = f"""# stackcut edit file, talking head. Times are seconds in the face take.
# graphic (one per line): {{text: "Zero *apps*"}} | {{icon: "🤖", label: Claude}} | {{count: 22, label: businesses}}
#   | {{list: [Plans, Books, Bills]}} | {{bars: [10, 24, 66], labels: [Jun, Jul, Aug]}} | {{image: shot.png}}
#   | {{chat: [{{in: "Can you call me?"}}, {{out: "Booked for 10:00 ✅"}}]}}
#   plus at: top | left | right, and word: a spoken word to land on.
# face_zoom: 1.15 is a zoom cut.  join: the sentence continues.  beat: seconds of pause held after a line.
layout: talk
face: {face}
av_offset: auto               # how late the take's sound is against its lips (a wireless mic); measured
hook: ""                      # one line on top, about 28 characters. Empty = no sticker.
music: null                   # optional: a music file for a quiet bed under the voice
style:
  tightness: tight            # every pause cut
fix: {{}}                       # caption corrections, word for word: {{stackcut: stackcut}}
lines:
"""
    else:
        head = f"""# stackcut edit file. Times are seconds in the FACE take.
# zoom: full | auto | [cx, cy, z]   (cx, cy = centre as fractions of the screen, z >= 1)
# screen_at: show this screen moment instead of the synced one.  join: the sentence continues.
# beat: seconds of pause held after a line (one short beat after the hook lands).
screen: {screen}
face: {face}
offset: {offset:.2f}          # face time = screen time + offset (measured from the audio)
av_offset: auto               # how late the face take's sound is against its lips (a wireless mic); measured
hook: ""                      # one line on top, about 28 characters. Empty = no sticker.
music: null                   # optional: a music file for a quiet bed under the voice
style:
  top_crop: 0.0               # e.g. 0.1 hides a browser's tabs and address bar in full view
privacy: []                   # blur boxes on the screen: - {{rect: [x, y, w, h], from: 0, to: 60}}
fix: {{}}                       # caption corrections, word for word: {{stream: screen}}
lines:
"""
    out.write_text(head + "\n".join(rows) + "\n")
    return out
