"""Check that the render keeps sound and picture together, with markers instead of lips.

    python tools/sync_markers.py mark face.mov face-marked.mp4   # a copy with sync markers
    (point an edit file's face: at the copy, av_offset: 0, voice: light, music: null, sfx: false; render it)
    python tools/sync_markers.py check out.mp4                  # how late each beep is against its flash

Every 45 frames one frame is brightened, and at that frame's exact time a 20 ms 3 kHz beep goes into the
sound. In the finished video each flash (in the face panel) should meet its beep within one frame.
On 21 Oct 2026 the stackcut chain (tight cuts, speed 1.07, frame-rate change) measured +20 ms median.
"""
import subprocess, sys
import numpy as np

FF = "ffmpeg"


def mark(src: str, out: str) -> None:
    fps = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=r_frame_rate",
                          "-of", "csv=p=0", src], capture_output=True, text=True).stdout.split("\n")[0].strip()
    rate = eval(fps)   # e.g. 30000/1001
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", src],
                               capture_output=True, text=True).stdout)
    graph = (f"[0:v:0]scale=-2:1080,eq=brightness=0.5:enable='eq(mod(n,45),0)'[v];"
             f"aevalsrc='if(lt(mod(t,45/{rate}),0.02),0.35*sin(2*PI*3000*t),0)':s=48000:d={dur:.3f}[b];"
             "[0:a:0][b]amix=inputs=2:normalize=0[a]")
    subprocess.run([FF, "-v", "error", "-y", "-i", src, "-filter_complex", graph, "-map", "[v]", "-map", "[a]",
                    "-c:v", "libx264", "-crf", "18", "-c:a", "aac", "-b:a", "256k", out], check=True)


def check(video: str, panel: str = "crop=1080:828:0:1092") -> None:
    raw = subprocess.run([FF, "-v", "error", "-i", video, "-vf", f"{panel},scale=108:83,format=gray", "-f", "rawvideo", "-"],
                         capture_output=True).stdout
    lum = np.frombuffer(raw, np.uint8).reshape(-1, 83 * 108).mean(1)
    flash_t = np.where(lum - np.convolve(lum, np.ones(7) / 7, "same") > 12)[0] / 30.0
    a = np.frombuffer(subprocess.run([FF, "-v", "error", "-i", video, "-vn", "-ac", "1", "-ar", "48000", "-af",
                                      "bandpass=f=3000:width_type=h:w=120,bandpass=f=3000:width_type=h:w=120",
                                      "-f", "f32le", "-"], capture_output=True).stdout, np.float32)
    e = np.sqrt((a[: len(a) // 96 * 96].reshape(-1, 96) ** 2).mean(1))   # 2 ms steps
    thr = np.percentile(e, 99.5) * 0.5
    beeps = []
    for i in np.where((e[1:] > thr) & (e[:-1] <= thr))[0] + 1:
        if not beeps or (i - beeps[-1]) * 0.002 > 0.3:
            beeps.append(i)
    beep_t = np.array(beeps) * 0.002
    d = [beep_t[np.argmin(np.abs(beep_t - f))] - f for f in flash_t if len(beep_t) and np.min(np.abs(beep_t - f)) < 0.1]
    d = np.array(d) * 1000
    print(f"{len(flash_t)} flashes, {len(beep_t)} beeps, {len(d)} pairs")
    if len(d):
        print(f"sound against picture: median {np.median(d):+.0f} ms, range {d.min():+.0f} to {d.max():+.0f} ms "
              f"({'within' if abs(np.median(d)) <= 33 else 'MORE than'} one frame)")


if __name__ == "__main__":
    {"mark": lambda: mark(sys.argv[2], sys.argv[3]), "check": lambda: check(sys.argv[2])}[sys.argv[1]]()
