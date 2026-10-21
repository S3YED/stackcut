"""FFmpeg plumbing: probing, SDR proxies, frame readers and encoder choice."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

import numpy as np

FPS = 30


def ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        sys.exit("stackcut needs ffmpeg on PATH (macOS: brew install ffmpeg, Linux: apt install ffmpeg)")
    return exe


def ffprobe() -> str:
    exe = shutil.which("ffprobe")
    if not exe:
        sys.exit("stackcut needs ffprobe on PATH (it ships with ffmpeg)")
    return exe


def run(args: list[str]) -> None:
    subprocess.run(args, check=True, stdin=subprocess.DEVNULL)


@lru_cache(maxsize=None)
def _ff_list(kind: str) -> str:
    return subprocess.run([ffmpeg(), "-hide_banner", f"-{kind}"], capture_output=True, text=True).stdout


def has_filter(name: str) -> bool:
    return f" {name} " in _ff_list("filters")


def has_encoder(name: str) -> bool:
    return f" {name} " in _ff_list("encoders")


@lru_cache(maxsize=None)
def probe(path: str) -> dict:
    out = subprocess.run([ffprobe(), "-v", "error", "-print_format", "json", "-show_streams", "-show_format",
                          str(path)], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def video_stream(path: str | Path) -> dict:
    return next(s for s in probe(str(path))["streams"] if s["codec_type"] == "video")


def duration(path: str | Path) -> float:
    return float(probe(str(path))["format"]["duration"])


def size(path: str | Path) -> tuple[int, int]:
    s = video_stream(path)
    return int(s["width"]), int(s["height"])


def is_hdr(path: str | Path) -> bool:
    return video_stream(path).get("color_transfer") in ("arib-std-b67", "smpte2084")


def rotation(path: str | Path) -> int:
    for sd in video_stream(path).get("side_data_list", []):
        if "rotation" in sd:
            return int(sd["rotation"])
    return 0


def is_10bit(path: str | Path) -> bool:
    return "10" in video_stream(path).get("pix_fmt", "")


def video_encoder(quality: str = "final") -> list[str]:
    """Hardware H.264 on a Mac, libx264 everywhere else."""
    if has_encoder("h264_videotoolbox"):
        rate = {"final": "12M", "proxy": "20M", "preview": "4500k"}[quality]
        return ["-c:v", "h264_videotoolbox", "-b:v", rate, "-profile:v", "high"]
    crf = {"final": "18", "proxy": "14", "preview": "26"}[quality]
    return ["-c:v", "libx264", "-crf", crf, "-preset", "medium" if quality == "final" else "veryfast"]


BT709 = ["-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709"]


def make_proxy(src: Path, out: Path) -> Path:
    """Upright, SDR, constant 30 fps copy at the source's own size, made once.

    iPhone HDR (HLG) is tone-mapped: in hardware on a Mac (VideoToolbox), with
    zscale elsewhere. Without either, the clip is used as is and looks flat."""
    if out.exists() and out.stat().st_mtime > src.stat().st_mtime and not rotation(out):
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    rot = rotation(src)
    turn = {-90: "transpose=1", 270: "transpose=1", 90: "transpose=2", -270: "transpose=2",
            180: "hflip,vflip", -180: "hflip,vflip"}.get(rot)
    if is_hdr(src) and has_filter("scale_vt"):
        dec = ["-hwaccel", "videotoolbox", "-hwaccel_output_format", "videotoolbox_vld", "-noautorotate"]
        if turn:   # turned by hand below: drop the phone's rotation tag, or every reader turns it again
            dec += ["-display_rotation:v:0", "0"]
        vf = ("scale_vt=color_matrix=bt709:color_primaries=bt709:color_transfer=bt709,hwdownload,"
              f"format={'p010le' if is_10bit(src) else 'nv12'},format=yuv420p")
        vf += f",{turn}" if turn else ""
    elif is_hdr(src) and has_filter("zscale"):
        dec, vf = [], ("zscale=t=linear:npl=203,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,"
                       "zscale=t=bt709:m=bt709:r=tv,format=yuv420p")
    else:
        if is_hdr(src):
            print(f"  note: {src.name} is HDR and this ffmpeg can't tone-map it (no scale_vt or zscale)",
                  file=sys.stderr)
        dec, vf = [], "format=yuv420p"
    run([ffmpeg(), "-v", "error", "-y", *dec, "-i", str(src), "-an", "-vf", f"{vf},fps={FPS}",
         *video_encoder("proxy"), "-pix_fmt", "yuv420p", *BT709, str(out)])
    return out


def extract_wav(src: Path, out: Path, rate: int = 16000, channels: int = 1) -> Path:
    if out.exists() and out.stat().st_mtime > src.stat().st_mtime:
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    run([ffmpeg(), "-v", "error", "-y", "-i", str(src), "-vn", "-ac", str(channels), "-ar", str(rate),
         "-c:a", "pcm_s16le", str(out)])
    return out


def frames(src: Path, start: float, count: int, w: int, h: int):
    """Yield `count` BGR frames from `start` (seconds), scaled to w x h: the first is the frame
    nearest to `start` (a seek lands on the first frame at or after it, so it starts half a frame
    early). Past the end of the file the last frame is held."""
    p = subprocess.Popen([ffmpeg(), "-v", "error", "-ss", f"{max(0.0, start - 0.5 / FPS):.4f}", "-i", str(src), "-an",
                          "-vf", f"fps={FPS},scale={w}:{h}:flags=lanczos,format=bgr24",
                          "-frames:v", str(count), "-f", "rawvideo", "-"],
                         stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)
    n, last = w * h * 3, None
    try:
        for _ in range(count):
            buf = p.stdout.read(n)
            if len(buf) == n:
                last = np.frombuffer(buf, np.uint8).reshape(h, w, 3)
            yield last
    finally:
        if p.poll() is None:   # stopped early (a B-roll card that leaves before its clip ends)
            p.kill()
        p.stdout.close()
        p.wait()
