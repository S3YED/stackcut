"""Sound: the cut voice track, an optional AI voice clean-up, a ducked music bed, -14 LUFS master."""
from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

from . import media


def cut_voice(src: Path, pieces: list[tuple[float, float]], out: Path) -> Path:
    """Concatenate the kept pieces of the face take's audio, with 12 ms fades at every cut (no clicks)."""
    parts, fade = [], 0.012
    filt = []
    for i, (a, b) in enumerate(pieces):
        d = b - a
        filt.append(f"[0:a]atrim={a:.3f}:{b:.3f},asetpts=PTS-STARTPTS,"
                    f"afade=t=in:d={fade},afade=t=out:st={max(0.0, d - fade):.3f}:d={fade}[p{i}]")
        parts.append(f"[p{i}]")
    filt.append("".join(parts) + f"concat=n={len(pieces)}:v=0:a=1[v]")
    media.run([media.ffmpeg(), "-v", "error", "-y", "-i", str(src), "-filter_complex", ";".join(filt),
               "-map", "[v]", "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", str(out)])
    return out


def isolate(src: Path, out: Path) -> Path | None:
    """ElevenLabs Voice Isolator when ELEVENLABS_API_KEY is set; otherwise None (ffmpeg clean-up only)."""
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        return None
    if out.exists() and out.stat().st_mtime > src.stat().st_mtime:
        return out
    boundary = "stackcut-boundary"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"audio\"; filename=\"voice.wav\"\r\n"
            "Content-Type: audio/wav\r\n\r\n").encode() + src.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request("https://api.elevenlabs.io/v1/audio-isolation", data=body, method="POST",
                                 headers={"xi-api-key": key,
                                          "Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            out.write_bytes(r.read())
        print("  voice isolated with ElevenLabs")
        return out
    except Exception as e:   # network, quota: fall back to the local chain
        print(f"  voice isolation skipped ({e}); using ffmpeg clean-up")
        return None


VOICE_CHAIN = ("highpass=f=80,afftdn=nf=-25,equalizer=f=250:t=q:w=1.2:g=-2.5,equalizer=f=3500:t=q:w=1.0:g=2,"
               "equalizer=f=9000:t=h:w=1:g=1.5,deesser=i=0.3,acompressor=threshold=-20dB:ratio=3:attack=8:release=120:"
               "makeup=3")


def master(voice: Path, out: Path, music: Path | None = None, music_lufs: float = -26.0,
           isolated: Path | None = None) -> Path:
    """Voice chain, optional music bed (looped, faded, side-chain ducked), final loudness -14 LUFS."""
    dur = media.duration(voice)
    v_in = isolated or voice
    args = [media.ffmpeg(), "-v", "error", "-y", "-i", str(v_in)]
    if music:
        args += ["-stream_loop", "-1", "-i", str(music)]
        # music level relative to the voice: -14 LUFS voice vs music_lufs
        gain = music_lufs + 14
        filt = (f"[0:a]{VOICE_CHAIN},aresample=48000,asplit=2[v][sc];"
                f"[1:a]aresample=48000,atrim=0:{dur:.3f},volume={gain}dB,"
                f"afade=t=in:d=0.4,afade=t=out:st={max(0.0, dur - 1.2):.3f}:d=1.2[m];"
                "[m][sc]sidechaincompress=threshold=0.05:ratio=6:attack=20:release=300[md];"
                "[v][md]amix=inputs=2:normalize=0,alimiter=limit=0.89,loudnorm=I=-14:TP=-1.5:LRA=11[out]")
    else:
        filt = f"[0:a]{VOICE_CHAIN},aresample=48000,alimiter=limit=0.89,loudnorm=I=-14:TP=-1.5:LRA=11[out]"
    args += ["-filter_complex", filt, "-map", "[out]", "-t", f"{dur:.3f}", "-ar", "48000", "-ac", "2",
             "-c:a", "pcm_s16le", str(out)]
    media.run(args)
    return out


def loudness(path: Path) -> float:
    """Integrated loudness (LUFS) of a file, for the report."""
    import subprocess
    r = subprocess.run([media.ffmpeg(), "-hide_banner", "-i", str(path), "-af", "loudnorm=print_format=json",
                        "-f", "null", "-"], capture_output=True, text=True)
    blob = r.stderr[r.stderr.rfind("{"):r.stderr.rfind("}") + 1]
    try:
        return float(json.loads(blob)["input_i"])
    except Exception:
        return float("nan")
