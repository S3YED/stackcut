"""Check every cut of an edit for clipped words: the voice still sounding where a piece ends, or
already sounding where it starts. Run after a first render (it reads the work folder's transcript):

    python tools/cut_audit.py edit.yaml

Cuts the render placed on purpose inside joined speech (a word running into the next) still show
here; every other flagged cut needs a look."""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stackcut import edit as editmod, render, speech  # noqa: E402

edit_path = Path(sys.argv[1]).resolve()
e = editmod.load(edit_path)
work = render.workdir(edit_path)
fwav = work / "face16k.wav"
words = json.load(open(work / "transcript.json"))["words"]
offset = speech.Offsets([(0.0, 0.0)])
pieces = render.cut(e, fwav, words, offset, {"screen": 1e9, "weblyfe": 1e9, "clark": 1e9})
x = speech.load_wav(fwav)
SR, HOP = 16000, 0.01
n = int(HOP * SR)
db = 20 * np.log10(np.sqrt((x[: len(x) // n * n].reshape(-1, n) ** 2).mean(1)) + 1e-9)
floor = float(np.percentile(db, 10))
def lv(t): i = int(t / HOP); return db[max(0, min(len(db) - 1, i))]
def word_at(t):
    return next((w for w in words if w["s"] - 0.02 <= t <= w["e"] + 0.02), None)
print(f"floor {floor:.1f} dB, {len(pieces)} pieces")
for k, p in enumerate(pieces):
    end = p.a + (p.t1 - p.t0)            # where the audio really stops (frame-rounded)
    if k + 1 < len(pieces) and abs(pieces[k + 1].a - end) < 1e-4:
        continue                          # the sound runs on into the next piece: no cut here
    # end: still sounding?
    tail = [lv(end + j * HOP) for j in range(0, 40)]
    before = lv(end - 0.015)
    if before > floor + 10:
        j = next((j for j, v in enumerate(tail) if v < floor + 8), None)
        real = end + (j if j is not None else 40) * HOP
        w = word_at(end - 0.03)
        nxt = min((ww["s"] for ww in words if ww["s"] > end - 0.01), default=99)
        flag = "CLIPPED END" if real - end > 0.03 else ""
        print(f"{k:2d} line {p.line + 1:2d} end {end:7.2f} level {before - floor:4.1f} dB over floor, voice runs to {real:7.2f} "
              f"(+{(real - end) * 1000:3.0f} ms) next word at {nxt:7.2f}  last '{w['w'] if w else '?'}'  {flag}")
    if k > 0 and abs(pieces[k - 1].a + (pieces[k - 1].t1 - pieces[k - 1].t0) - p.a) < 1e-4:
        continue
    head_before = lv(p.a - 0.02)
    if head_before > floor + 10:
        w = word_at(p.a + 0.03)
        print(f"{k:2d} line {p.line + 1:2d} START {p.a:7.2f} voice already {head_before - floor:4.1f} dB over floor 20 ms before  first '{w['w'] if w else '?'}'")
