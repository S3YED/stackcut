"""The talking-head layout: the face fills the frame and makes room when a graphic needs it.

  full         the face fills the 9:16 frame; captions below the chin, the hook sticker on top
  right, left  the face slides to one side and the graphic stands next to the head
  top          the split look: a gradient on top carries the graphic, the face sits below,
               captions on the seam
"""
from __future__ import annotations

import numpy as np

from . import captions, face, graphics, screen

W, H = 1080, 1920
FACE_TOP = 1092                         # the face panel's top in the split look (as in layout: split)
CAPTION_FULL, CAPTION_SPLIT = 1330, 1040
MOVE = 0.42                             # seconds the face takes to make room
STAY = 0.35                             # graphics this close together keep the face where it is
LEAD = 0.3                              # the face starts making room this long before a graphic lands
ANCHOR = {"top": (W // 2, 656), "right": (W - 190, 760), "left": (190, 760)}
MODES = {   # (split 0-1, face centre x, face centre y as fractions of the face panel, face closer by)
    "full": (0.0, 0.5, 0.42, 1.0),
    "right": (0.0, 0.33, 0.42, 1.0),    # a graphic on the right: the face moves left
    "left": (0.0, 0.67, 0.42, 1.0),
    "top": (1.0, 0.5, 0.46, 1.4),       # the split look, the face a little closer than in layout: split
}


def segments(gs: list[graphics.Graphic], starts: list[float], cuts: list[bool]) -> list[tuple[float, float, str, bool]]:
    """Where the face is made room for: (from, to, mode, hard cut), from just before each graphic
    lands (never before its line starts) to the moment it is gone; neighbours in the same place
    merge, so the face doesn't bounce."""
    out: list[list] = []
    for g in sorted(gs, key=lambda g: g.t0):
        a = max(starts[g.line], g.t0 - LEAD)
        if out and out[-1][2] == g.at and a - out[-1][1] < STAY:
            out[-1][1] = max(out[-1][1], g.t1)
        else:
            out.append([a, g.t1, g.at, cuts[g.line]])
    return [tuple(s) for s in out]


class Talk:
    """Draws one frame of the talking-head layout. Graphics carry their own timing (graphics.schedule)."""

    def __init__(self, gs: list[graphics.Graphic], starts: list[float], cuts: list[bool], style: dict,
                 look: face.Look, accent: tuple[int, int, int], lang: str):
        self.graphics, self.look, self.accent, self.lang = gs, look, accent, lang
        self.bg = screen.background(W, H, style["background"], style["base"])
        self.fade = np.linspace(0.55, 0, 90, dtype=np.float32)[:, None, None]
        self.switches: list[tuple[float, tuple, tuple, bool]] = []
        segs = segments(gs, starts, cuts)
        for i, (a, b, mode, cut) in enumerate(segs):
            self._switch(a, mode, cut)
            nxt = segs[i + 1][0] if i + 1 < len(segs) else None
            if nxt is None or nxt - b >= STAY:
                self._switch(b, "full", False)

    def _switch(self, t: float, mode: str, cut: bool) -> None:
        self.switches.append((t, self.params(t), MODES[mode], cut))

    def params(self, t: float) -> tuple[float, float, float, float]:
        cur = MODES["full"]
        for ts, frm, to, cut in self.switches:
            if t < ts:
                break
            k = 1.0 if cut else screen.ease((t - ts) / MOVE)
            cur = tuple(a + (b - a) * k for a, b in zip(frm, to))
        return cur

    def moves(self) -> list[float]:
        """When the face starts making room for a split (a soft whoosh goes there)."""
        return [ts for ts, frm, to, cut in self.switches if to[0] > frm[0] + 0.5]

    def draw(self, tt: float, ff: np.ndarray | None, cam: tuple[float, float, float],
             fill: float) -> tuple[np.ndarray, int, float]:
        """The frame, and where the captions go (y, opacity)."""
        s, xa, ya, closer = self.params(tt)
        ph = max(H - FACE_TOP, int(round(H - s * FACE_TOP)))
        canvas = self.bg.copy() if s > 0.002 or ff is None else np.empty((H, W, 3), np.uint8)
        if ff is not None:
            fp = self.look.panel(ff, cam, W, ph, fill * closer, ya, xa)
            if s > 0.002:     # the face panel's top edge melts into the gradient
                top = fp[:90].astype(np.float32) * (1 - self.fade * s)
                fp[:90] = top.astype(np.uint8)
            canvas[H - ph:] = fp
        for g in self.graphics:
            out = graphics.frame(g, tt, self.accent, self.lang)
            if out is not None and out[0] is not None:
                layer, dx, dy = out
                ax, ay = ANCHOR[g.at]
                captions.blend(canvas, layer, ax + dx, ay + dy)
        # the captions sit below the chin, or on the seam in the split look; on the way between,
        # they fade out and back in rather than slide across the face
        if s < 0.5:
            return canvas, CAPTION_FULL, 1 - graphics.clip((s - 0.03) / 0.15)
        return canvas, CAPTION_SPLIT, graphics.clip((s - 0.78) / 0.17)
