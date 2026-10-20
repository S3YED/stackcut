"""Sound: the cut voice track, an optional AI voice clean-up, a ducked music bed, -14 LUFS master."""
from __future__ import annotations

import json
import os
import urllib.request
import wave
from pathlib import Path

import numpy as np

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


VOICE_CHAINS = {
    # the microphone's own sound: rumble cut, a little presence, gentle compression (a DJI or lavalier mic)
    "light": "highpass=f=70,equalizer=f=4500:t=q:w=1.2:g=1.5,acompressor=threshold=-18dB:ratio=2:attack=10:"
             "release=150:makeup=2",
    # a phone or laptop mic in a noisy room: denoise, less mud, de-ess, firmer compression
    "clean": "highpass=f=80,afftdn=nf=-25,equalizer=f=250:t=q:w=1.2:g=-2.5,equalizer=f=3500:t=q:w=1.0:g=2,"
             "equalizer=f=9000:t=h:w=1:g=1.5,deesser=i=0.3,acompressor=threshold=-20dB:ratio=3:attack=8:release=120:"
             "makeup=3",
}
TARGET_LUFS, CEILING = -14.0, 10 ** (-1.5 / 20)   # Reels/TikTok loudness, true-peak ceiling -1.5 dB


def sfx_track(dur: float, out: Path, sr: int = 48000) -> Path:
    """Two small sound cues, synthesised (no sample libraries, nothing to license): a soft low impact
    under the first frame, a light pop as the hook sticker lands, and a pop on the last frame."""
    n = int(dur * sr)
    track = np.zeros(n, np.float32)
    rng = np.random.default_rng(3)

    def impact() -> np.ndarray:
        t = np.arange(int(0.5 * sr)) / sr
        body = np.sin(2 * np.pi * (58 - 18 * t) * t) * np.exp(-t / 0.09)
        burst = np.convolve(rng.normal(0, 1, len(t)), np.ones(24) / 24, "same") * np.exp(-t / 0.012)
        x = body + 0.6 * burst
        return (x / np.abs(x).max() * 10 ** (-9 / 20)).astype(np.float32)

    def pop(db: float) -> np.ndarray:
        t = np.arange(int(0.08 * sr)) / sr
        x = np.sin(2 * np.pi * (1300 - 5000 * t) * t) * np.exp(-t / 0.018)
        return (x / np.abs(x).max() * 10 ** (db / 20)).astype(np.float32)

    for at, cue in ((0.0, impact()), (0.02, pop(-16)), (max(0.0, dur - 0.12), pop(-14))):
        i = int(at * sr)
        j = min(n, i + len(cue))
        track[i:j] += cue[: j - i]
    pcm = (np.clip(np.repeat(track[:, None], 2, 1), -1, 1) * 32767).astype(np.int16)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return out


def master(voice: Path, out: Path, music: Path | None = None, music_lufs: float = -26.0,
           isolated: Path | None = None, sfx: Path | None = None, chain: str = "light", speed: float = 1.0) -> Path:
    """The voice through its chain and set to -14 LUFS; under it the music bed at `music_lufs` (looped,
    faded, side-chain ducked) and the sound cues; then one fixed gain and a peak limiter for the whole
    mix (no level pumping). speed: the finished mix a touch faster, pitch kept."""
    dur = media.duration(voice)
    v_proc = out.with_suffix(".voice.wav")
    media.run([media.ffmpeg(), "-v", "error", "-y", "-i", str(isolated or voice), "-af",
               f"{VOICE_CHAINS[chain]},aresample=48000", "-ac", "2", "-c:a", "pcm_s24le", str(v_proc)])
    args = [media.ffmpeg(), "-v", "error", "-y", "-i", str(v_proc)]
    mixes, filt = ["[v]"], f"[0:a]volume={TARGET_LUFS - loudness(v_proc):.2f}dB"
    if music:
        args += ["-stream_loop", "-1", "-i", str(music)]
        filt += (",asplit=2[v][sc];"
                 f"[1:a]aresample=48000,atrim=0:{dur:.3f},volume={music_lufs - loudness(music):.2f}dB,"
                 f"afade=t=in:d=0.4,afade=t=out:st={max(0.0, dur - 1.2):.3f}:d=1.2[m];"
                 "[m][sc]sidechaincompress=threshold=0.05:ratio=6:attack=20:release=300[md]")
        mixes.append("[md]")
    else:
        filt += "[v]"
    if sfx:
        args += ["-i", str(sfx)]
        filt += f";[{len(mixes)}:a]aresample=48000[fx]"
        mixes.append("[fx]")
    tempo = f",atempo={speed}" if abs(speed - 1.0) > 1e-3 else ""
    filt += (f";{''.join(mixes)}amix=inputs={len(mixes)}:normalize=0{tempo}[out]" if len(mixes) > 1
             else f";[v]anull{tempo}[out]")
    raw = out.with_suffix(".raw.wav")
    media.run(args + ["-filter_complex", filt, "-map", "[out]", "-t", f"{dur / speed:.3f}", "-ar", "48000",
                      "-ac", "2", "-c:a", "pcm_s24le", str(raw)])
    media.run([media.ffmpeg(), "-v", "error", "-y", "-i", str(raw), "-af",
               f"volume={TARGET_LUFS - loudness(raw):.2f}dB,alimiter=limit={CEILING:.4f}:attack=5:release=60:"
               "level=disabled", "-ar", "48000", "-c:a", "pcm_s24le", str(out)])
    raw.unlink()
    v_proc.unlink()
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
