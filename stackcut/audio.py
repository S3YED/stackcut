"""Sound: the cut voice track, noise removal that keeps the voice natural, a ducked music bed,
-14 LUFS master."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import tempfile
import urllib.request
import wave
from pathlib import Path

import numpy as np

from . import media


def cut_voice(src: Path, pieces: list[tuple[float, float, bool]], out: Path) -> Path:
    """Concatenate the kept pieces of the face take's audio, with 12 ms fades at every cut (no clicks)
    and 30 ms where a cut falls inside joined speech. Pieces that continue each other are one stretch."""
    joined: list[list] = []
    for a, b, soft in pieces:
        if joined and abs(a - joined[-1][1]) < 1e-4:
            joined[-1][1], joined[-1][2] = b, soft
        else:
            joined.append([a, b, soft])
    filt, parts = [], []
    for i, (a, b, soft) in enumerate(joined):
        d, f_in, f_out = b - a, 0.012, 0.03 if soft else 0.012
        filt.append(f"[0:a]atrim={a:.3f}:{b:.3f},asetpts=PTS-STARTPTS,"
                    f"afade=t=in:d={f_in},afade=t=out:st={max(0.0, d - f_out):.3f}:d={f_out}[p{i}]")
        parts.append(f"[p{i}]")
    filt.append("".join(parts) + f"concat=n={len(joined)}:v=0:a=1[v]")
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


DEEPFILTER = "https://github.com/Rikorose/DeepFilterNet/releases/download/v0.5.6/"
DEEPFILTER_BUILDS = {   # DeepFilterNet 3 (MIT / Apache-2.0) command-line builds, checksums pinned
    ("Darwin", "arm64"): ("deep-filter-0.5.6-aarch64-apple-darwin",
                          "4601e7f4e4c03e59a4c5b5000216ef3add3e808799cfccd95e14e83ea4611081"),
    ("Linux", "x86_64"): ("deep-filter-0.5.6-x86_64-unknown-linux-musl",
                          "70775e251eee44c0f2451a1e833326cf8bcbbe304d3e7cd12851e6fce72ef7da"),
    ("Linux", "aarch64"): ("deep-filter-0.5.6-aarch64-unknown-linux-gnu",
                           "14e02a1c0028f3ca0bdf83b62b3336e56ba0556894ef295a95e8573f06557166"),
}


def deep_filter() -> str | None:
    """The DeepFilterNet binary: `deep-filter` on PATH, or the official release fetched once into
    ~/.cache/stackcut/bin (checksum checked). None on a platform without a pinned build."""
    on_path = shutil.which("deep-filter")
    if on_path:
        return on_path
    build = DEEPFILTER_BUILDS.get((platform.system(), platform.machine()))
    if not build:
        return None
    name, sha = build
    path = Path.home() / ".cache" / "stackcut" / "bin" / name
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        print(f"  downloading the DeepFilterNet denoiser ({name}, about 30 MB, once)")
        urllib.request.urlretrieve(DEEPFILTER + name, path)
    if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
        path.unlink()
        raise SystemExit(f"DeepFilterNet checksum mismatch; delete {path} and try again")
    path.chmod(0o755)
    return str(path)


def denoise(src: Path, out: Path, limit_db: float = 24.0) -> Path | None:
    """Room noise out, voice untouched: DeepFilterNet 3 at 48 kHz. limit_db caps the noise
    reduction by mixing a little of the original back in, so the voice keeps its air and breath
    and the pauses never go to dead silence. None when no build is available."""
    exe = deep_filter()
    if exe is None:
        print("  no DeepFilterNet build for this machine; put `deep-filter` on PATH. Using the mic as is.")
        return None
    with tempfile.TemporaryDirectory() as tmp:
        mono = Path(tmp) / "voice.wav"
        media.run([media.ffmpeg(), "-v", "error", "-y", "-i", str(src), "-ac", "1", "-ar", "48000",
                   "-c:a", "pcm_s16le", str(mono)])
        subprocess.run([exe, "-D", "-a", f"{limit_db:g}", "-o", str(Path(tmp) / "out"), str(mono)],
                       check=True, capture_output=True)
        shutil.move(str(Path(tmp) / "out" / "voice.wav"), out)
    print(f"  room noise removed with DeepFilterNet (limit {limit_db:g} dB)")
    return out


VOICE_CHAINS = {
    # the microphone's own sound: rumble cut, a little presence, gentle compression, a soft de-ess
    "light": "highpass=f=70,equalizer=f=4500:t=q:w=1.2:g=1.5,acompressor=threshold=-18dB:ratio=2:attack=10:"
             "release=150:makeup=2,deesser=i=0.25:m=0.5:f=0.5",
    # a phone or laptop mic: FFmpeg denoise, less mud, firmer compression, de-ess
    "clean": "highpass=f=80,afftdn=nf=-25,equalizer=f=250:t=q:w=1.2:g=-2.5,equalizer=f=3500:t=q:w=1.0:g=2,"
             "equalizer=f=9000:t=h:w=1:g=1.5,deesser=i=0.3,acompressor=threshold=-20dB:ratio=3:attack=8:release=120:"
             "makeup=3",
}
DENOISED_AIR = ",equalizer=f=7000:t=h:w=0.7:g=2"   # gives back the ~2 dB of 4-10 kHz the denoiser takes
TARGET_LUFS, CEILING = -14.0, 10 ** (-1.5 / 20)   # Reels/TikTok loudness, true-peak ceiling -1.5 dB


def sfx_track(dur: float, out: Path, cues: list[tuple[float, str]] = (), sr: int = 48000) -> Path:
    """Small sound cues, synthesised (no sample libraries, nothing to license): a soft low impact
    under the first frame, a light pop as the hook sticker lands and a pop on the last frame.
    `cues` adds (seconds, "pop" | "whoosh"): a quieter pop as a graphic lands, a soft whoosh for a card."""
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

    def whoosh(db: float) -> np.ndarray:   # band-limited air that swells and fades, about 0.35 s
        m = int(0.36 * sr)
        spec = np.fft.rfft(rng.normal(0, 1, m))
        f = np.fft.rfftfreq(m, 1 / sr)
        spec *= np.exp(-((np.log(np.maximum(f, 1)) - np.log(1800)) ** 2) / 0.9)
        x = np.fft.irfft(spec, m) * np.sin(np.pi * np.linspace(0, 1, m) ** 0.7) ** 2
        return (x / np.abs(x).max() * 10 ** (db / 20)).astype(np.float32)

    made = {"pop": pop(-22), "whoosh": whoosh(-27)}
    for at, cue in ((0.0, impact()), (0.02, pop(-16)), (max(0.0, dur - 0.12), pop(-14)),
                    *((t, made[k]) for t, k in cues if 0.3 < t < dur - 0.3)):
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


def _read(path: Path) -> np.ndarray:
    raw = subprocess.run([media.ffmpeg(), "-v", "error", "-i", str(path), "-ac", "2", "-ar", "48000", "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).reshape(-1, 2).copy()


def _write(x: np.ndarray, path: Path) -> Path:
    p = subprocess.run([media.ffmpeg(), "-v", "error", "-y", "-f", "f32le", "-ar", "48000", "-ac", "2", "-i", "-",
                        "-c:a", "pcm_f32le", str(path)], input=x.astype(np.float32).tobytes())
    if p.returncode:
        raise SystemExit(f"could not write {path.name}")
    return path


def duck_curve(voice: np.ndarray, depth_db: float, attack: float = 0.08, release: float = 0.4) -> np.ndarray:
    """Per-sample gain (dB) for the music: down `depth_db` while the voice talks, back up in the
    pauses (the beat after the hook, the end), with a quick attack and a slow release."""
    hop = 480   # 10 ms
    mono = voice.mean(1)
    n = len(mono) // hop
    db = 20 * np.log10(np.sqrt((mono[: n * hop].reshape(n, hop) ** 2).mean(1)) + 1e-9)
    talking = (db > np.percentile(db, 95) - 30).astype(np.float32)
    env, a, r = np.zeros(n, np.float32), 1 - np.exp(-0.01 / attack), 1 - np.exp(-0.01 / release)
    for i in range(n):
        prev = env[i - 1] if i else talking[0]
        env[i] = prev + (a if talking[i] > prev else r) * (talking[i] - prev)
    curve = np.interp(np.arange(len(mono)), np.arange(n) * hop + hop / 2, env)
    return -depth_db * curve


def master(voice: Path, out: Path, music: Path | None = None, music_lufs: float = -32.0,
           cleaned: Path | None = None, sfx: bool = True, chain: str = "light", speed: float = 1.0,
           music_start: float = 0.0, duck_db: float = 4.0, mid_cut_db: float = 4.0,
           cues: list[tuple[float, str]] = ()) -> dict:
    """The voice (cleaned or the mic as is) through its chain, sped up if asked, set to -14 LUFS.
    Under it the music bed: its 1-4 kHz scooped out so it never sits on the voice, at `music_lufs`
    in the pauses and `duck_db` lower while he talks. Then the sound cues (with the graphics' `cues`),
    one fixed gain and a peak limiter for the whole mix (no level pumping). Returns the loudness of the parts."""
    work = out.parent
    tempo = f",atempo={speed}" if abs(speed - 1.0) > 1e-3 else ""
    air = DENOISED_AIR if cleaned is not None else ""
    v_path = work / "voice_chain.wav"
    media.run([media.ffmpeg(), "-v", "error", "-y", "-i", str(cleaned or voice), "-af",
               f"{VOICE_CHAINS[chain]}{air}{tempo},aresample=48000", "-ac", "2", "-c:a", "pcm_f32le", str(v_path)])
    v = _read(v_path) * 10 ** ((TARGET_LUFS - loudness(v_path)) / 20)
    dur = len(v) / 48000
    mix, report = v.copy(), {"voice_lufs": TARGET_LUFS}
    if music:
        b_path = work / "bed.wav"
        media.run([media.ffmpeg(), "-v", "error", "-y", "-ss", f"{music_start:.3f}", "-stream_loop", "-1", "-i", str(music),
                   "-t", f"{dur:.3f}", "-af", f"aresample=48000,highpass=f=60,"
                   f"equalizer=f=2000:t=o:w=2:g=-{mid_cut_db:g},afade=t=in:d=0.4,"
                   f"afade=t=out:st={max(0.0, dur - 0.6):.3f}:d=0.6", "-ac", "2", "-c:a", "pcm_f32le", str(b_path)])
        bed = _read(b_path)[: len(v)] * 10 ** ((music_lufs - loudness(b_path)) / 20)
        bed = bed * (10 ** (duck_curve(v, duck_db)[: len(bed)] / 20))[:, None]
        mix[: len(bed)] += bed
        report["bed_lufs"] = loudness(_write(bed, work / "bed_mixed.wav"))
    if sfx:
        fx = _read(sfx_track(dur, work / "sfx.wav", cues))[: len(v)]
        mix[: len(fx)] += fx
    raw = _write(mix, work / "mix_raw.wav")
    media.run([media.ffmpeg(), "-v", "error", "-y", "-i", str(raw), "-af",
               f"volume={TARGET_LUFS - loudness(raw):.2f}dB,alimiter=limit={CEILING:.4f}:attack=5:release=60:"
               "level=disabled", "-ar", "48000", "-c:a", "pcm_s24le", str(out)])
    report["mix_lufs"] = loudness(out)
    for tmp in (v_path, raw, work / "bed.wav", work / "bed_mixed.wav", work / "sfx.wav"):
        tmp.unlink(missing_ok=True)
    return report


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
