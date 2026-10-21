"""Render an edit file into a 1080x1920 short.

layout: split   the screen on top in a rounded window, the face below, captions on the seam
layout: talk    the face fills the frame, animated graphics per line (talk.py, graphics.py)"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import audio, captions, edit as editmod, face, graphics, lipsync, media, screen, speech, talk

W, H, FPS = 1080, 1920, media.FPS
FACE_TOP = 1092              # split: face panel from here to the bottom
WIN_X, WIN_W = 40, 1000      # split: the screen window
WIN_BAND = (340, 1000)       # split: vertical room for the window
HOOK_Y, CAPTION_Y = 268, 1040


@dataclass
class Piece:
    line: int
    a: float          # face-take seconds (audio)
    b: float
    screen_in: float  # screen seconds at the piece's first frame (split)
    t0: float = 0.0   # output seconds
    t1: float = 0.0


def workdir(edit_path: Path) -> Path:
    d = edit_path.resolve().parent / f".{edit_path.stem}.stackcut"
    d.mkdir(exist_ok=True)
    return d


def prepare(face_path: Path, work: Path, model: str = "large-v3-turbo"):
    """The face take's proxy, audio and transcript, cached in the work folder."""
    print("· preparing sources")
    fproxy = media.make_proxy(face_path, work / "face.mp4")
    fwav = media.extract_wav(face_path, work / "face16k.wav")
    transcript = speech.transcribe(fwav, work / "transcript.json", model)
    return fproxy, fwav, transcript


def resolve_offset(e: editmod.Edit, face_wav: Path, work: Path) -> float:
    if e.offset != "auto":
        return float(e.offset)
    if not any(s["codec_type"] == "audio" for s in media.probe(str(e.screen))["streams"]):
        raise SystemExit("the screen recording has no audio, so sync can't be measured: set 'offset:' by hand")
    swav = media.extract_wav(e.screen, work / "screen16k.wav")
    off, conf = speech.sync_offset(face_wav, swav)
    print(f"· sync: face = screen + {off:.2f} s (confidence {conf:.2f})")
    if conf < 0.3:
        print("  warning: weak sync. Were both recorded at the same time? Set 'offset:' by hand if not.")
    return off


def resolve_av_offset(e: editmod.Edit, pieces: list, work: Path) -> float:
    """How late the face take's sound is against its lips, and the gate: with a measurement at hand,
    a set av_offset more than one frame off the lips stops the render."""
    a0 = min(p.a for p in pieces)
    a1 = min(max(p.b for p in pieces), a0 + 150)
    cache = work / "lipsync.json"
    key = f"{e.face.name}:{a0:.2f}-{a1:.2f}"
    seen = json.loads(cache.read_text()) if cache.exists() else {}
    m = seen.get(key)
    if m is None:
        try:
            print(f"· lip sync: measuring the face take ({a0:.0f}-{a1:.0f} s)")
            m = lipsync.measure(e.face, a0, a1, ffmpeg=media.ffmpeg())
        except lipsync.Unavailable as err:
            if e.av_offset == "auto":
                raise SystemExit(f"av_offset: auto needs a lip-sync measurement ({err}). Or set av_offset by hand: "
                                 "0 for a phone recording")
            print(f"  lip sync not checked ({err})")
            return float(e.av_offset)
        seen[key] = m
        cache.write_text(json.dumps(seen))
    print(f"  the take's sound is {m['lag'] * 1000:+.0f} ms against the lips (r {m['r']:.2f}, "
          f"80% range {m['lo'] * 1000:+.0f} to {m['hi'] * 1000:+.0f} ms)")
    if m["r"] < lipsync.MIN_R:
        if e.av_offset == "auto":
            raise SystemExit("lip sync: lips and sound don't line up clearly enough to measure; set av_offset by hand")
        print("  too unclear to check; using av_offset as set")
        return float(e.av_offset)
    if e.av_offset == "auto":
        return float(m["lag"])
    off = float(m["lag"]) - float(e.av_offset)
    if abs(off) > 1 / FPS:
        raise SystemExit(f"lip sync gate: with av_offset {e.av_offset} the sound would be {off * 1000:+.0f} ms off the "
                         f"lips, more than one frame. Set av_offset: {m['lag']:.3f}")
    return float(e.av_offset)


def cut(e: editmod.Edit, fwav: Path, words_all: list[dict], offset: float, sdur: float) -> list[Piece]:
    """Every line, with its pauses cut out, laid on the output timeline."""
    st = e.style
    pieces: list[Piece] = []
    for i, ln in enumerate(e.lines):
        runs = (speech.tighten(fwav, words_all, ln.start, ln.end, ln.pad_in, st.get("tightness", "normal"))
                if ln.tight else [(ln.start, ln.end)])
        if ln.beat and runs:   # a held pause after the line: the take's own silence, up to the next word
            a, b = runs[-1]
            nxt = min((w["s"] for w in words_all if w["s"] >= b), default=b + ln.beat)
            runs[-1] = (a, max(b, min(b + ln.beat, nxt - 0.02)))
        used = 0.0
        for a, b in runs:
            s_in = (ln.screen_at + used) if ln.screen_at is not None else a - offset
            pieces.append(Piece(i, a, b, min(max(0.0, s_in), max(0.0, sdur - 0.1))))
            used += b - a
    t = 0.0
    for p in pieces:
        n = int(round((p.b - p.a) * FPS))
        p.t0, p.t1 = t, t + n / FPS
        t = p.t1
    return pieces


def caption_words(e: editmod.Edit, pieces: list[Piece], words_all: list[dict]) -> tuple[list[speech.Word], list[int]]:
    """The spoken words on the output timeline (with the edit's fixes), and the line each belongs to."""
    words, lines, seen = [], [], set()
    for k, p in enumerate(pieces):
        for w in speech.words_in(words_all, p.a, p.b):
            if (w["s"], w["w"]) in seen:
                continue
            seen.add((w["s"], w["w"]))
            a = p.t0 + max(0.0, w["s"] - p.a)
            b = p.t0 + min(p.b, w["e"]) - p.a
            text = w["w"]
            core = re.sub(r"[^\w'-]", "", text).lower()
            if core in e.fix:   # caption correction from the edit file, punctuation kept
                text = re.sub(re.escape(re.sub(r"[^\w'-]", "", text)), e.fix[core], text, count=1)
            if not words or re.search(r"[.?!]$", words[-1].text):   # a sentence starts: capital letter
                text = text[:1].upper() + text[1:]
            words.append(speech.Word(text, a, max(a + 0.08, b)))
            lines.append(p.line)
        nxt = pieces[k + 1] if k + 1 < len(pieces) else None
        joined = nxt is not None and (nxt.line == p.line or e.lines[nxt.line].join)
        if words and not joined and not re.search(r"[.?!]$", words[-1].text):
            words[-1].text = re.sub(r"[,;:]+$", "", words[-1].text) + "."   # a line that ends mid-sentence ends here
    return words, lines


def plan_graphics(e: editmod.Edit, pieces: list[Piece], words: list[speech.Word], word_lines: list[int]):
    """Each line's graphic with its timing: it covers `span` lines, up to the next line with its own."""
    starts = [min(p.t0 for p in pieces if p.line == i) for i in range(len(e.lines))]
    ends = [max(p.t1 for p in pieces if p.line == i) for i in range(len(e.lines))]
    gs = []
    for i, ln in enumerate(e.lines):
        if ln.graphic is None:
            continue
        kind, spec, at = ln.graphic
        last = i
        for j in range(i + 1, min(len(e.lines), i + int(spec.get("span", 1)))):
            if e.lines[j].graphic is not None:
                break
            last = j
        g = graphics.Graphic(kind, spec, at, i)
        said = [(graphics.core(w.text), w.start) for w, li in zip(words, word_lines) if i <= li <= last]
        graphics.schedule(g, starts[i], ends[last], said)
        gs.append(g)
    return gs, starts


class Split:
    """layout: split. The screen in a rounded window that eases to each line's zoom, the face below."""

    def __init__(self, e: editmod.Edit, sproxy: Path, pieces: list[Piece], look: face.Look):
        st = e.style
        self.e, self.sproxy, self.look = e, sproxy, look
        self.top_crop = float(st.get("top_crop", 0.0))
        self.sw, self.sh = media.size(sproxy)
        win_h, win_w = round(WIN_W * self.sh / self.sw), WIN_W
        if win_h > WIN_BAND[1] - WIN_BAND[0]:             # a tall screen recording: fit the height
            win_h = WIN_BAND[1] - WIN_BAND[0]
            win_w = round(win_h * self.sw / self.sh)
        win_y = WIN_BAND[0] + (WIN_BAND[1] - WIN_BAND[0] - win_h) // 2
        self.win = screen.Window(W, H, (W - win_w) // 2, win_y, win_w, win_h, 28, st["background"], st["base"])
        self.blurs = [(r["rect"], float(r.get("from", 0)), float(r.get("to", 1e9))) for r in e.privacy]
        self.fade = np.linspace(0.55, 0, 90, dtype=np.float32)[:, None, None]
        self.targets = []
        for i, ln in enumerate(e.lines):   # the zoom target per line
            if ln.zoom == "full":
                z = [0.5, 1.0, 1 / (1 - self.top_crop)] if self.top_crop else [0.5, 0.5, 1.0]
            elif ln.zoom == "auto":
                ps = [p for p in pieces if p.line == i]
                z = screen.auto_zoom(sproxy, ps[0].screen_in, ps[-1].screen_in + (ps[-1].b - ps[-1].a),
                                     top_crop=self.top_crop)
            else:
                z = [float(v) for v in ln.zoom]
            self.targets.append(z)
        self.cam, self.cur_line, self.line_t0, self.line_start_cam = None, -1, 0.0, None

    def start(self, p: Piece, n: int) -> None:
        if p.line != self.cur_line:
            self.cur_line = p.line
            self.line_start_cam = (tuple(self.targets[p.line]) if self.cam is None or self.e.lines[p.line].cut
                                   else self.cam)
            self.line_t0 = p.t0
        self.p, self.target = p, tuple(self.targets[p.line])
        self.frames = media.frames(self.sproxy, p.screen_in, n, self.sw, self.sh)

    def draw(self, k: int, tt: float, ff: np.ndarray | None, cam_at: tuple, fill: float) -> tuple[np.ndarray, int, float]:
        sf = next(self.frames)
        e_k = screen.ease((tt - self.line_t0) / 0.6)
        self.cam = tuple(a0 + (b0 - a0) * e_k for a0, b0 in zip(self.line_start_cam, self.target))
        canvas = self.win.bg.copy()
        if sf is not None:
            s_t = self.p.screen_in + k / FPS
            rects = [r for r, a, b in self.blurs if a <= s_t <= b]
            if rects:
                sf = screen.blur(sf, rects)
            self.win.draw(canvas, screen.view(sf, *self.cam, self.win.w, self.win.h, self.top_crop))
        if ff is not None:
            fpanel = self.look.panel(ff, cam_at, W, H - FACE_TOP, fill).astype(np.float32)
            fpanel[:90] *= 1 - self.fade
            canvas[FACE_TOP:] = fpanel.astype(np.uint8)
        return canvas, CAPTION_Y, 1.0


class Talk:
    """layout: talk, adapted to the frame loop (talk.Talk does the drawing)."""

    def __init__(self, layout: talk.Talk):
        self.layout = layout

    def start(self, p: Piece, n: int) -> None:
        pass

    def draw(self, k: int, tt: float, ff: np.ndarray | None, cam_at: tuple, fill: float) -> tuple[np.ndarray, int, float]:
        return self.layout.draw(tt, ff, cam_at, fill)


def render(edit_path: Path, out: Path, model: str = "large-v3-turbo", preview: bool = False) -> Path:
    e = editmod.load(edit_path)
    st = e.style
    work = workdir(edit_path)
    fproxy, fwav, tr = prepare(e.face, work, model)
    words_all = tr["words"]
    sproxy = media.make_proxy(e.screen, work / "screen.mp4") if e.layout == "split" else None
    offset = resolve_offset(e, fwav, work) if sproxy else 0.0
    sdur = media.duration(sproxy) if sproxy else 0.0

    pieces = cut(e, fwav, words_all, offset, sdur)
    total = pieces[-1].t1
    print(f"· {e.layout} layout: {len(e.lines)} lines -> {len(pieces)} pieces, {total:.1f} s")
    words, word_lines = caption_words(e, pieces, words_all)
    pages = captions.paginate(words, int(st["caption_words"]), int(st["caption_chars"]))
    (work / "words.json").write_text(json.dumps([{"t": w.text, "s": round(w.start, 2), "e": round(w.end, 2)}
                                                  for w in words], ensure_ascii=False))
    av = resolve_av_offset(e, pieces, work)   # the picture is taken that much earlier than the sound

    accent = tuple(int(st["accent"].lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    cam_track = face.Smooth(face.track(fproxy, work / "face_track.json"))
    fg = st["face_grade"]
    look = face.Look(face.grade_lut(face.white_balance(fproxy, cam_track, float(st["white_balance"])),
                                    float(fg["brightness"]), float(fg["warmth"])), float(fg["eyes"]))
    speed = float(st.get("speed", 1.0))
    cues: list[tuple[float, str]] = []
    if e.layout == "talk":
        gs, starts = plan_graphics(e, pieces, words, word_lines)
        layout = talk.Talk(gs, starts, [ln.cut for ln in e.lines], st, look, accent, tr.get("language") or "en")
        cues = [c for g in gs for c in graphics.cues(g)] + [(t, "whoosh") for t in layout.moves()]
        frame_layout = Talk(layout)
        (work / "graphics.json").write_text(json.dumps(
            [{"line": g.line + 1, "kind": g.kind, "at": g.at, "t0": round(g.t0, 2), "t1": round(g.t1, 2),
              "beats": [round(b, 2) for b in g.beats]} for g in gs]))
        print(f"  {len(gs)} graphics")
    else:
        frame_layout = Split(e, sproxy, pieces, look)
        (work / "zoom.json").write_text(json.dumps(frame_layout.targets))

    # sound
    print("· sound")
    voice = audio.cut_voice(e.face, [(p.a, p.a + (p.t1 - p.t0)) for p in pieces], work / "voice.wav")
    cleaned = (audio.isolate(voice, work / "voice_isolated.mp3") if st["voice"] == "isolate" else
               audio.denoise(voice, work / "voice_denoised.wav", float(st["denoise_db"])) if st["voice"] == "denoise"
               else None)
    mix = work / "mix.wav"
    levels = audio.master(voice, mix, e.music, float(st["music_lufs"]), cleaned, bool(st.get("sfx", True)),
                          chain="clean" if st["voice"] == "clean" else "light", speed=speed,
                          music_start=float(st["music_start"]), duck_db=float(st["music_duck_db"]),
                          mid_cut_db=float(st["music_mid_cut_db"]), cues=[(t / speed, kind) for t, kind in cues])
    if "bed_lufs" in levels:
        print(f"  voice {levels['voice_lufs']:.1f} LUFS, music bed {levels['bed_lufs']:.1f} LUFS "
              f"({levels['voice_lufs'] - levels['bed_lufs']:.0f} dB under the voice)")

    # picture
    print("· picture")
    fw, fh = media.size(fproxy)
    fill = float(st["face_fill"])
    # punch-ins: the face steps in a little on key words, at most every 2.2 s, so a jump cut reads as a beat
    punches, last = [], -1e9
    if st.get("punch"):
        for pg in pages:
            if pg.start - last >= 2.2:
                punches.append(pg.start)
                last = pg.start
    def punch_at(t: float) -> float:
        n = sum(1 for x in punches if x <= t)
        return 1.10 if n % 2 else 1.0
    accent_bgr = accent[::-1]
    hook = captions.sticker(e.hook) if e.hook else None

    venc = media.video_encoder("preview" if preview else "final")
    # speed: the frames go in a touch faster and come out at 30 fps, matching the sped-up mix (one encode)
    enc = subprocess.Popen([media.ffmpeg(), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
                            "-s", f"{W}x{H}", "-r", f"{FPS * speed:.4f}", "-i", "-", "-i", str(mix),
                            "-map", "0:v", "-map", "1:a", "-vf", f"fps={FPS}",
                            *venc, "-pix_fmt", "yuv420p", *media.BT709, "-af", "apad", "-c:a", "aac", "-b:a", "320k",
                            "-t", f"{total / speed:.3f}", "-movflags", "+faststart", str(out)], stdin=subprocess.PIPE)
    frame_i = 0
    try:
        for p in pieces:
            n = int(round((p.t1 - p.t0) * FPS))
            frame_layout.start(p, n)
            ff_iter = media.frames(fproxy, p.a - av, n, fw, fh)
            zoom = e.lines[p.line].face_zoom
            for k in range(n):
                tt = p.t0 + k / FPS
                canvas, cap_y, cap_a = frame_layout.draw(k, tt, next(ff_iter), cam_track.at(p.a - av + k / FPS),
                                                         fill * zoom * punch_at(tt))
                captions.progress(canvas, (frame_i + 1) / (total * FPS), accent_bgr)
                if hook is not None:
                    captions.blend(canvas, hook, W // 2, HOOK_Y)
                cap = captions.caption_at(pages, tt, accent)
                if cap is not None and cap_a > 0.01:
                    captions.blend(canvas, cap, W // 2, cap_y, cap_a)
                enc.stdin.write(canvas.tobytes())
                frame_i += 1
    finally:
        enc.stdin.close()
        enc.wait()
    total /= speed
    sheet(out, out.with_suffix(".sheet.jpg"))
    print(f"✓ {out}  ({total:.1f} s, {len(words)} words, {audio.loudness(out):.1f} LUFS)")
    print(f"  review: {out.with_suffix('.sheet.jpg')} and {work / 'words.json'}")
    return out


def sheet(video: Path, out: Path, every: float = 0.5, cols: int = 10) -> Path:
    """Contact sheet: one frame every `every` seconds. Read it before you post."""
    n = int(media.duration(video) / every) + 1
    rows = (n + cols - 1) // cols
    media.run([media.ffmpeg(), "-v", "error", "-y", "-i", str(video), "-vf",
               f"fps={1 / every},scale=180:320,tile={cols}x{rows}", "-frames:v", "1", str(out)])
    return out
