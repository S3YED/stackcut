"""Render an edit file into a 1080x1920 short: screen on top, face below, captions on the seam."""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import audio, captions, edit as editmod, face, media, screen, speech

W, H, FPS = 1080, 1920, media.FPS
FACE_TOP = 1092              # face panel from here to the bottom
WIN_X, WIN_W = 40, 1000      # the screen window
WIN_BAND = (340, 1000)       # vertical room for the window
HOOK_Y, CAPTION_Y = 268, 1040


@dataclass
class Piece:
    line: int
    a: float          # face-take seconds (audio)
    b: float
    screen_in: float  # screen seconds at the piece's first frame
    t0: float = 0.0   # output seconds
    t1: float = 0.0


def workdir(edit_path: Path) -> Path:
    d = edit_path.resolve().parent / f".{edit_path.stem}.stackcut"
    d.mkdir(exist_ok=True)
    return d


def prepare(screen_path: Path, face_path: Path, work: Path, model: str = "large-v3-turbo"):
    """Proxies, audio, transcript and sync, all cached in the work folder."""
    print("· preparing sources")
    fproxy = media.make_proxy(face_path, work / "face.mp4")
    sproxy = media.make_proxy(screen_path, work / "screen.mp4")
    fwav = media.extract_wav(face_path, work / "face16k.wav")
    transcript = speech.transcribe(fwav, work / "transcript.json", model)
    return fproxy, sproxy, fwav, transcript


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


def render(edit_path: Path, out: Path, model: str = "large-v3-turbo", preview: bool = False) -> Path:
    e = editmod.load(edit_path)
    st = e.style
    work = workdir(edit_path)
    fproxy, sproxy, fwav, tr = prepare(e.screen, e.face, work, model)
    words_all = tr["words"]
    offset = resolve_offset(e, fwav, work)
    sdur = media.duration(sproxy)
    top_crop = float(st.get("top_crop", 0.0))

    # 1. pieces: every line, with its pauses cut out
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
    total = t
    print(f"· {len(e.lines)} lines -> {len(pieces)} pieces, {total:.1f} s")

    # 2. zoom target per line
    targets = []
    for i, ln in enumerate(e.lines):
        if ln.zoom == "full":
            z = [0.5, 1.0, 1 / (1 - top_crop)] if top_crop else [0.5, 0.5, 1.0]
        elif ln.zoom == "auto":
            ps = [p for p in pieces if p.line == i]
            z = screen.auto_zoom(sproxy, ps[0].screen_in, ps[-1].screen_in + (ps[-1].b - ps[-1].a), top_crop=top_crop)
        else:
            z = [float(v) for v in ln.zoom]
        targets.append(z)
    (work / "zoom.json").write_text(json.dumps(targets))

    # 3. captions: words mapped onto the output timeline
    words, seen = [], set()
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
        nxt = pieces[k + 1] if k + 1 < len(pieces) else None
        joined = nxt is not None and (nxt.line == p.line or e.lines[nxt.line].join)
        if words and not joined and not re.search(r"[.?!]$", words[-1].text):
            words[-1].text = re.sub(r"[,;:]+$", "", words[-1].text) + "."   # a line that ends mid-sentence ends here
    pages = captions.paginate(words, int(st["caption_words"]), int(st["caption_chars"]))
    (work / "words.json").write_text(json.dumps([{"t": w.text, "s": round(w.start, 2), "e": round(w.end, 2)}
                                                  for w in words], ensure_ascii=False))

    # 4. sound
    print("· sound")
    voice = audio.cut_voice(e.face, [(p.a, p.a + (p.t1 - p.t0)) for p in pieces], work / "voice.wav")
    cleaned = (audio.isolate(voice, work / "voice_isolated.mp3") if st["voice"] == "isolate" else
               audio.denoise(voice, work / "voice_denoised.wav", float(st["denoise_db"])) if st["voice"] == "denoise"
               else None)
    speed = float(st.get("speed", 1.0))
    mix = work / "mix.wav"
    levels = audio.master(voice, mix, e.music, float(st["music_lufs"]), cleaned, bool(st.get("sfx", True)),
                          chain="clean" if st["voice"] == "clean" else "light", speed=speed,
                          music_start=float(st["music_start"]), duck_db=float(st["music_duck_db"]),
                          mid_cut_db=float(st["music_mid_cut_db"]))
    if "bed_lufs" in levels:
        print(f"  voice {levels['voice_lufs']:.1f} LUFS, music bed {levels['bed_lufs']:.1f} LUFS "
              f"({levels['voice_lufs'] - levels['bed_lufs']:.0f} dB under the voice)")

    # 5. picture
    print("· picture")
    sw, sh = media.size(sproxy)
    fw, fh = media.size(fproxy)
    win_h = round(WIN_W * sh / sw)
    win_w = WIN_W
    if win_h > WIN_BAND[1] - WIN_BAND[0]:                 # a tall screen recording: fit the height
        win_h = WIN_BAND[1] - WIN_BAND[0]
        win_w = round(win_h * sw / sh)
    win_y = WIN_BAND[0] + (WIN_BAND[1] - WIN_BAND[0] - win_h) // 2
    win = screen.Window(W, H, (W - win_w) // 2, win_y, win_w, win_h, 28, st["background"], st["base"])
    cam_track = face.Smooth(face.track(fproxy, work / "face_track.json"))
    fg = st["face_grade"]
    lut = face.grade_lut(face.white_balance(fproxy, cam_track, float(st["white_balance"])),
                         float(fg["brightness"]), float(fg["warmth"]))
    eyes = face.eye_light(W, H - FACE_TOP, float(fg["eyes"]), fill=float(st["face_fill"]))
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
    accent = tuple(int(st["accent"].lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    accent_bgr = accent[::-1]
    hook = captions.sticker(e.hook) if e.hook else None
    fade = np.linspace(0.55, 0, 90, dtype=np.float32)[:, None, None]
    blurs = [(r["rect"], float(r.get("from", 0)), float(r.get("to", 1e9))) for r in e.privacy]

    venc = media.video_encoder("preview" if preview else "final")
    # speed: the frames go in a touch faster and come out at 30 fps, matching the sped-up mix (one encode)
    enc = subprocess.Popen([media.ffmpeg(), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
                            "-s", f"{W}x{H}", "-r", f"{FPS * speed:.4f}", "-i", "-", "-i", str(mix),
                            "-map", "0:v", "-map", "1:a", "-vf", f"fps={FPS}",
                            *venc, "-pix_fmt", "yuv420p", *media.BT709, "-af", "apad", "-c:a", "aac", "-b:a", "320k",
                            "-t", f"{total / speed:.3f}", "-movflags", "+faststart", str(out)], stdin=subprocess.PIPE)
    cam, line_start_cam, cur_line, frame_i = None, None, -1, 0
    try:
        for p in pieces:
            n = int(round((p.t1 - p.t0) * FPS))
            ln = e.lines[p.line]
            if p.line != cur_line:
                cur_line = p.line
                line_start_cam = tuple(targets[p.line]) if cam is None or ln.cut else cam
                line_t0 = p.t0
            target = tuple(targets[p.line])
            sf_iter = media.frames(sproxy, p.screen_in, n, sw, sh)
            ff_iter = media.frames(fproxy, p.a, n, fw, fh)
            for k in range(n):
                tt = p.t0 + k / FPS
                sf, ff = next(sf_iter), next(ff_iter)
                e_k = screen.ease((tt - line_t0) / 0.6)
                cam = tuple(a0 + (b0 - a0) * e_k for a0, b0 in zip(line_start_cam, target))
                canvas = win.bg.copy()
                if sf is not None:
                    s_t = p.screen_in + k / FPS
                    rects = [r for r, a, b in blurs if a <= s_t <= b]
                    if rects:
                        sf = screen.blur(sf, rects)
                    win.draw(canvas, screen.view(sf, *cam, win.w, win.h, top_crop))
                if ff is not None:
                    fpanel = face.panel(face.apply_lut(ff, lut), cam_track.at(p.a + k / FPS), W, H - FACE_TOP,
                                        float(st["face_fill"]) * punch_at(tt)).astype(np.float32)
                    if eyes is not None:
                        fpanel = np.clip(fpanel * eyes, 0, 255)
                    fpanel[:90] *= 1 - fade
                    canvas[FACE_TOP:] = fpanel.astype(np.uint8)
                captions.progress(canvas, (frame_i + 1) / (total * FPS), accent_bgr)
                if hook is not None:
                    captions.blend(canvas, hook, W // 2, HOOK_Y)
                cap = captions.caption_at(pages, tt, accent)
                if cap is not None:
                    captions.blend(canvas, cap, W // 2, CAPTION_Y)
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
