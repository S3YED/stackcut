"""stackcut: turn a talking-head take (with or without a screen recording) into a vertical short.

  stackcut doctor                         check ffmpeg, encoders and Python packages
  stackcut draft FACE [-o edit.yaml]      talking head: transcribe and write a first edit (layout: talk)
  stackcut draft SCREEN FACE [-o edit.yaml]   screen + face: transcribe, sync and write a first edit (layout: split)
  stackcut render edit.yaml [-o out.mp4]  render the edit (add --preview for a small fast file)
  stackcut lines FACE [--energy]          the transcript as numbered sentences with times (and take energy)
  stackcut frames SCREEN [--every 2]      a contact sheet of the screen recording (to pick zooms and blur)
  stackcut sync FACE [--from S --to S]    how late the face take's sound is against the lips (av_offset)
"""
from __future__ import annotations

import argparse
import importlib
import os
import platform
import shutil
import sys
from pathlib import Path

from . import __version__, audio, edit, lipsync, media, render, speech


def cmd_doctor(_: argparse.Namespace) -> None:
    ok = True
    print(f"stackcut {__version__}")
    print(f"  ffmpeg: {media.ffmpeg()}")
    for f, why in (("scale_vt", "iPhone HDR tone-mapping on a Mac"), ("zscale", "HDR tone-mapping elsewhere"),
                   ("sidechaincompress", "music ducking"), ("loudnorm", "loudness")):
        print(f"  filter {f:18s} {'yes' if media.has_filter(f) else 'no ':3s}  ({why})")
    enc = "h264_videotoolbox" if media.has_encoder("h264_videotoolbox") else "libx264"
    print(f"  encoder {enc}")
    if not media.has_encoder("libx264") and enc == "libx264":
        print("  ! no H.264 encoder found"); ok = False
    for mod in ("numpy", "cv2", "PIL", "faster_whisper", "yaml"):
        try:
            importlib.import_module(mod)
            print(f"  python {mod:16s} ok")
        except ImportError:
            print(f"  ! python {mod} missing (pip install -e .)"); ok = False
    import cv2
    if not hasattr(cv2, "FaceDetectorYN"):
        print("  ! OpenCV is too old for the YuNet face detector (need 4.8+)"); ok = False
    df = shutil.which("deep-filter") or audio.DEEPFILTER_BUILDS.get((platform.system(), platform.machine()))
    print("  noise removal (voice: denoise):", "DeepFilterNet" + ("" if shutil.which("deep-filter") else
          " (downloads on first use)") if df else "not available here: put `deep-filter` on PATH, or use voice: light")
    print("  voice: isolate:", "ElevenLabs key set" if os.environ.get("ELEVENLABS_API_KEY") else "needs ELEVENLABS_API_KEY")
    sys.exit(0 if ok else 1)


def cmd_lines(a: argparse.Namespace) -> None:
    face = Path(a.face)
    work = face.resolve().parent / f".{face.stem}.stackcut"
    work.mkdir(exist_ok=True)
    wav = media.extract_wav(face, work / "face16k.wav")
    tr = speech.transcribe(wav, work / "transcript.json", a.model, a.language)
    for s in speech.sentences(tr["words"]):
        extra = ""
        if a.energy:   # to pick the take said with the most conviction
            m = speech.energy(wav, tr["words"], s["start"], s["end"])
            extra = (f"  [{m['loud_db']:5.1f} dB, pitch {m['pitch_range']:4.1f} st, "
                     f"{m['words_per_s']:.1f} w/s, pause {m['longest_pause']:.2f}]")
        print(f"{s['i']:3d}  {s['start']:7.2f}-{s['end']:7.2f}{extra}  {s['text']}")


def cmd_sync(a: argparse.Namespace) -> None:
    face = Path(a.face)
    t1 = a.to if a.to is not None else min(media.duration(face), a.start + 150)
    m = lipsync.measure(face, a.start, t1, ffmpeg=media.ffmpeg())
    print(f"{face.name} {a.start:.0f}-{t1:.0f} s: the sound is {m['lag'] * 1000:+.0f} ms against the lips "
          f"(r {m['r']:.2f}, 80% range {m['lo'] * 1000:+.0f} to {m['hi'] * 1000:+.0f} ms, face found {m['faces']:.0%})")
    if m["r"] < lipsync.MIN_R:
        print("  unclear: too little talking to the camera in this stretch; try another --from/--to")
    else:
        print(f"  edit.yaml: av_offset: {m['lag']:.3f}")


def cmd_draft(a: argparse.Namespace) -> None:
    if len(a.files) not in (1, 2):
        raise SystemExit("draft takes FACE (talking head) or SCREEN FACE (screen recording + face)")
    face_p = Path(a.files[-1])
    screen_p = Path(a.files[0]) if len(a.files) == 2 else None
    out = Path(a.out)
    work = out.resolve().parent / f".{out.stem}.stackcut"
    work.mkdir(exist_ok=True)
    fwav = media.extract_wav(face_p, work / "face16k.wav")
    tr = speech.transcribe(fwav, work / "transcript.json", a.model)
    rel = lambda p: os.path.relpath(os.path.abspath(p), os.path.dirname(os.path.abspath(out)))
    if screen_p is None:
        edit.draft(rel(face_p), speech.sentences(tr["words"]), out, speech.speech_runs(fwav))
    else:
        swav = media.extract_wav(screen_p, work / "screen16k.wav")
        off, conf = speech.sync_offset(fwav, swav)
        print(f"· sync: face = screen + {off:.2f} s (confidence {conf:.2f})")
        edit.draft(rel(face_p), speech.sentences(tr["words"]), out, speech.speech_runs(fwav), rel(screen_p), off,
                   media.duration(screen_p))
    print(f"✓ wrote {out}. Edit it (or ask your AI to), then: stackcut render {out}")


def cmd_render(a: argparse.Namespace) -> None:
    p = Path(a.edit)
    out = Path(a.out) if a.out else p.with_suffix(".mp4")
    render.render(p, out, a.model, a.preview)


def cmd_frames(a: argparse.Namespace) -> None:
    src = Path(a.screen)
    out = Path(a.out) if a.out else src.with_suffix(".frames.jpg")
    n = int(media.duration(src) / a.every) + 1
    media.run([media.ffmpeg(), "-v", "error", "-y", "-i", str(src), "-vf",
               f"fps={1 / a.every},scale=384:-2,tile=5x{(n + 4) // 5}", "-frames:v", "1",
               str(out)])
    print(f"✓ {out}  (one frame every {a.every} s, left to right, top to bottom)")


def main() -> None:
    ap = argparse.ArgumentParser(prog="stackcut", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="large-v3-turbo", help="whisper model (small is faster, large-v3-turbo better)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor").set_defaults(fn=cmd_doctor)
    s = sub.add_parser("lines"); s.add_argument("face")
    s.add_argument("--energy", action="store_true", help="add loudness, pitch movement, pace and pauses per sentence")
    s.add_argument("--language", help="the take's language (nl, en, ...): set it when a take opens in another language")
    s.set_defaults(fn=cmd_lines)
    s = sub.add_parser("sync"); s.add_argument("face")
    s.add_argument("--from", dest="start", type=float, default=0.0); s.add_argument("--to", type=float)
    s.set_defaults(fn=cmd_sync)
    s = sub.add_parser("draft"); s.add_argument("files", nargs="+", metavar="[SCREEN] FACE")
    s.add_argument("-o", "--out", default="edit.yaml"); s.set_defaults(fn=cmd_draft)
    s = sub.add_parser("render"); s.add_argument("edit"); s.add_argument("-o", "--out")
    s.add_argument("--preview", action="store_true"); s.set_defaults(fn=cmd_render)
    s = sub.add_parser("frames"); s.add_argument("screen"); s.add_argument("--every", type=float, default=2.0)
    s.add_argument("-o", "--out"); s.set_defaults(fn=cmd_frames)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
