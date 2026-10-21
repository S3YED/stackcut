"""Animated graphics for the talking-head layout, drawn with Pillow and blended onto each frame.

One graphic per line, set in edit.yaml as `graphic:`. Each is a pure function of time: it lands as
the line starts or on a spoken word (`word:`), stays for the line (or `span` lines) and leaves just
before the cut.

  text   a keyword callout: the words land one by one, *starred* words in the accent colour
  icon   an emoji or a logo file on a white tile, with an optional label chip
  count  a number that counts up, with a label
  list   chips with a tick that land one by one, each on its own spoken word
  bars   a bar chart that grows, the last bar in the accent colour
  image  a picture or a B-roll clip in a rounded card that slides in
  chat   message bubbles that land one by one (a UI card)
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from . import media
from .captions import CAPTION_FONT, EMOJI, STICKER_FONT, _emoji_font, font

KINDS = ("text", "icon", "count", "list", "bars", "image", "chat")
PLACES = ("top", "left", "right")
DEFAULT_AT = {"text": "right", "icon": "right", "count": "top", "list": "right", "bars": "top",
              "image": "top", "chat": "top"}
TOP_ONLY = ("bars", "chat")                     # too wide to stand next to the head
VIDEO = {".mp4", ".mov", ".m4v", ".webm"}
PICTURE = {".png", ".jpg", ".jpeg", ".webp"}
BOX = {"top": (940, 600), "left": (340, 600), "right": (340, 600)}   # the room a graphic may take
IN, OUT = 0.32, 0.2                             # seconds to land and to leave
INK, MUTED, PAPER = (8, 11, 18), (92, 101, 120), (255, 255, 255)
SOFT = (238, 241, 246)                          # an incoming chat bubble


@dataclass
class Graphic:
    kind: str
    spec: dict
    at: str
    line: int
    t0: float = 0.0                              # lands (output seconds, before speed)
    t1: float = 0.0                              # gone
    beats: list[float] = field(default_factory=list)   # list items and chat bubbles land at these times
    state: dict = field(default_factory=dict)


def parse(raw: dict, root: Path, where: str) -> tuple[str, dict, str]:
    """Check a line's `graphic:` and return (kind, spec, place). Errors name the line."""
    if not isinstance(raw, dict):
        raise SystemExit(f"{where}: graphic must be a mapping, e.g. {{text: \"Zero *apps*\"}}")
    if True in raw or False in raw:   # YAML reads a bare on/off/yes/no key as a boolean
        raise SystemExit(f"{where}: write word: (the spoken word to land on), not on:")
    kinds = [k for k in KINDS if k in raw]
    if len(kinds) != 1:
        raise SystemExit(f"{where}: graphic needs exactly one of {', '.join(KINDS)}")
    kind = kinds[0]
    spec = dict(raw)
    at = spec.pop("at", DEFAULT_AT[kind])
    if at not in PLACES:
        raise SystemExit(f"{where}: graphic at must be one of {', '.join(PLACES)}")
    if kind in TOP_ONLY and at != "top":
        raise SystemExit(f"{where}: a {kind} graphic is too wide for the side; use at: top")
    if int(spec.get("span", 1)) < 1:
        raise SystemExit(f"{where}: span is a number of lines, 1 or more")
    v = spec[kind]
    if kind == "count":
        try:
            spec["count"] = float(v)
            spec["from"] = float(spec.get("from", 0))
        except (TypeError, ValueError):
            raise SystemExit(f"{where}: count must be a number") from None
    elif kind == "bars":
        if not isinstance(v, list) or len(v) < 2 or not all(isinstance(x, (int, float)) for x in v):
            raise SystemExit(f"{where}: bars must be a list of 2 or more numbers")
        for key in ("labels", "values"):
            if spec.get(key) and len(spec[key]) != len(v):
                raise SystemExit(f"{where}: bars has {len(v)} bars but {len(spec[key])} {key}")
    elif kind == "list":
        if not isinstance(v, list) or not v or len(v) > 5:
            raise SystemExit(f"{where}: list must have 1 to 5 items")
        spec["list"] = [str(x) for x in v]
    elif kind == "chat":
        if not isinstance(v, list) or not v or len(v) > 4:
            raise SystemExit(f"{where}: chat must have 1 to 4 messages, each {{in: ...}} or {{out: ...}}")
        msgs = []
        for m in v:
            side = next((s for s in ("in", "out") if isinstance(m, dict) and s in m), None)
            if side is None:
                raise SystemExit(f"{where}: every chat message is {{in: \"...\"}} or {{out: \"...\"}}")
            msgs.append({"side": side, "text": str(m[side]), "word": m.get("word")})
        spec["chat"] = msgs
    elif kind in ("icon", "image"):
        v = str(v)
        if kind == "icon" and EMOJI.fullmatch(v.strip()):
            spec["icon"] = v.strip()
        else:
            p = (root / v).resolve()
            ok = PICTURE | (VIDEO if kind == "image" else set())
            if p.suffix.lower() not in ok:
                raise SystemExit(f"{where}: {kind} must be {'an emoji or ' if kind == 'icon' else ''}"
                                 f"a {', '.join(sorted(ok))} file")
            if not p.exists():
                raise SystemExit(f"{where}: {kind} file not found: {p}")
            spec[kind] = p
    else:
        spec["text"] = str(v)
    return kind, spec, at


# ---------------------------------------------------------------- timing

def core(word: str) -> str:
    return re.sub(r"[^\w'-]", "", word).lower()


def _spoken(word: str | None, words: list[tuple[str, float]], after: float = -1.0) -> float | None:
    """When the first word of `word` is said (a spoken word that starts with it), after `after`."""
    if not word or not str(word).split():
        return None
    want = core(str(word).split()[0])
    return next((s for w, s in words if s > after and want and w.startswith(want)), None)


def schedule(g: Graphic, t0: float, t1: float, words: list[tuple[str, float]]) -> None:
    """Set when the graphic lands, its beats and when it is gone. `words` are (core, output start)
    of the lines it covers; t0-t1 is their span on the output timeline."""
    land = _spoken(g.spec.get("word"), words)
    g.t0 = (land - 0.06) if land is not None else t0 + 0.1
    if g.at == "top":               # the face makes room first
        g.t0 = max(g.t0, t0 + 0.24)
    g.t0 = min(g.t0, max(t0, t1 - 0.8))
    g.t1 = t1
    if g.kind in ("list", "chat"):
        items = g.spec[g.kind]
        cue_words = items if g.kind == "list" else [m["word"] for m in items]   # list items land on their own word
        step = min(0.85 if g.kind == "list" else 1.1, max(0.25, (t1 - 0.5 - g.t0) / len(items)))
        beats, last = [], g.t0 - step
        for word in cue_words:
            c = _spoken(word, words, last + 0.15)
            b = (c - 0.06) if c is not None else last + step
            beats.append(max(g.t0, min(b, t1 - 0.8)))   # each one readable before the cut
            last = beats[-1]
        g.beats = beats


def cues(g: Graphic) -> list[tuple[float, str]]:
    """Sound cues: a soft pop as a graphic (or each list item and bubble) lands, a whoosh for a card."""
    if g.kind == "image":
        return [(max(0.0, g.t0 - 0.05), "whoosh")]
    if g.beats:
        return [(b, "pop") for b in g.beats]
    return [(g.t0, "pop")]


# ---------------------------------------------------------------- easing

def clip(x: float) -> float:
    return min(max(x, 0.0), 1.0)


def out_cubic(x: float) -> float:
    return 1 - (1 - clip(x)) ** 3


def out_back(x: float, s: float = 1.2) -> float:
    x = clip(x) - 1
    return 1 + (s + 1) * x ** 3 + s * x ** 2


def out_expo(x: float) -> float:
    x = clip(x)
    return 1.0 if x >= 1 else 1 - 2 ** (-10 * x)


def presence(g: Graphic, t: float) -> tuple[float, float]:
    """(how far it has landed 0-1, how present it still is 0-1)."""
    return clip((t - g.t0) / IN), clip((g.t1 - t) / OUT)


# ---------------------------------------------------------------- drawing helpers

def _runs(text: str, with_emoji: bool) -> list[tuple[str, bool]]:
    runs, pos = [], 0
    for m in EMOJI.finditer(text) if with_emoji else []:
        if m.start() > pos:
            runs.append((text[pos:m.start()], False))
        runs.append((m.group(), True))
        pos = m.end()
    if pos < len(text):
        runs.append((text[pos:], False))
    return runs


def text_w(text: str, size: int, path: Path = CAPTION_FONT) -> float:
    f = font(str(path), size)
    ef, es = _emoji_font(size)
    if ef is None:
        return f.getlength(EMOJI.sub("", text))
    return sum(ef.getlength(t) * es if e else f.getlength(t) for t, e in _runs(text, True))


def draw_text(img: Image.Image, x: float, y: float, text: str, size: int, fill: tuple, path: Path = CAPTION_FONT) -> None:
    """Text with colour emoji, top-left at (x, y)."""
    f = font(str(path), size)
    ef, es = _emoji_font(size)
    d = ImageDraw.Draw(img)
    asc, _ = f.getmetrics()
    for t, is_emoji in _runs(text if ef else EMOJI.sub("", text), ef is not None):
        if is_emoji:
            e = Image.new("RGBA", (int(ef.getlength(t)) + 4, ef.size + 20))
            ImageDraw.Draw(e).text((0, 0), t, font=ef, embedded_color=True)
            e = e.resize((max(1, int(e.width * es)), max(1, int(e.height * es))), Image.LANCZOS)
            if len(fill) == 4 and fill[3] < 255:
                a = np.asarray(e).copy()
                a[..., 3] = (a[..., 3] * (fill[3] / 255)).astype(np.uint8)
                e = Image.fromarray(a)
            put(img, e, x, y + (asc - size) * 0.6)
            x += ef.getlength(t) * es
        else:
            d.text((x, y), t, font=f, fill=fill)
            x += f.getlength(t)


def wrap(words: list[str], size: int, max_w: float, path: Path = CAPTION_FONT) -> list[list[int]]:
    space = font(str(path), size).getlength(" ")
    lines, cur, cur_w = [], [], 0.0
    for i, w in enumerate(words):
        ww = text_w(w, size, path)
        if cur and cur_w + space + ww > max_w:
            lines.append(cur)
            cur, cur_w = [], 0.0
        cur_w += (space if cur else 0) + ww
        cur.append(i)
    if cur:
        lines.append(cur)
    return lines


MARGIN = 44   # room around a card for its shadow


@lru_cache(maxsize=64)
def card(w: int, h: int, radius: int = 30, fill: tuple = PAPER + (255,), shadow: float = 0.34) -> Image.Image:
    """A rounded card with a soft drop shadow, on a transparent canvas with MARGIN room around it.
    Cached: copy it before drawing on it."""
    m = MARGIN
    size = (w + 2 * m, h + 2 * m)
    img = Image.new("RGBA", size)
    if shadow:
        sh = Image.new("L", size)
        ImageDraw.Draw(sh).rounded_rectangle([m, m + 12, m + w, m + h + 12], radius=radius, fill=int(255 * shadow))
        img = Image.merge("RGBA", (*Image.new("RGB", size).split(), sh.filter(ImageFilter.GaussianBlur(18))))
    ImageDraw.Draw(img).rounded_rectangle([m, m, m + w, m + h], radius=radius, fill=fill)
    return img


def finish(img: Image.Image, alpha: float, scale: float = 1.0, angle: float = 0.0) -> np.ndarray | None:
    if alpha <= 0.01 or scale <= 0.02:
        return None
    if abs(angle) > 0.05:
        img = img.rotate(angle, resample=Image.BICUBIC, expand=True)
    if abs(scale - 1) > 0.002:
        img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.BILINEAR)
    a = np.asarray(img)
    if alpha < 0.999:
        a = a.copy()
        a[..., 3] = (a[..., 3] * alpha).astype(np.uint8)
    return a


def put(dst: Image.Image, layer, x: float, y: float) -> None:
    """Alpha-composite `layer` (an image or an RGBA array) at (x, y), clipped to `dst`."""
    if layer is None:
        return
    src = layer if isinstance(layer, Image.Image) else Image.fromarray(layer)
    x, y = int(x), int(y)
    sx, sy = max(0, -x), max(0, -y)
    w, h = min(src.width - sx, dst.width - max(0, x)), min(src.height - sy, dst.height - max(0, y))
    if w > 0 and h > 0:
        dst.alpha_composite(src, (max(0, x), max(0, y)), (sx, sy, sx + w, sy + h))


def mix(c: tuple, k: float, base: tuple = PAPER) -> tuple:
    return tuple(int(base[i] + (c[i] - base[i]) * k) for i in range(3))


def number(x: float, decimals: int, lang: str) -> str:
    s = f"{x:,.{decimals}f}"
    if lang in ("nl", "de", "es", "it", "pt", "fr"):
        s = s.replace(",", "\0").replace(".", ",").replace("\0", ".")
    return s


def compact(x: float, lang: str) -> str:
    if abs(x) >= 1e6:
        s = f"{x / 1e6:.1f}".rstrip("0").rstrip(".") + "M"
    elif abs(x) >= 1e4:
        s = f"{x / 1e3:.0f}k"
    elif abs(x) >= 1e3:
        s = f"{x / 1e3:.1f}".rstrip("0").rstrip(".") + "k"
    else:
        s = f"{x:g}"
    return s.replace(".", ",") if lang in ("nl", "de", "es", "it", "pt", "fr") else s


# ---------------------------------------------------------------- the graphics

def frame(g: Graphic, t: float, accent: tuple, lang: str = "en") -> tuple[np.ndarray, int, int] | None:
    """The graphic at output time t: an RGBA layer (RGB order) and its offset from the anchor."""
    if t >= g.t1 and "frames" in g.state:   # a B-roll clip's reader is done
        g.state.pop("frames").close()
    if t < g.t0 - 0.001 or t >= g.t1:
        return None
    return DRAW[g.kind](g, t, accent, lang)


def _text(g: Graphic, t: float, accent: tuple, lang: str):
    raw = g.spec["text"]
    toks, hot, inside = [], [], False
    for part in re.split(r"(\*)", raw):     # *starred* words in the accent colour
        if part == "*":
            inside = not inside
            continue
        for w in part.split():
            toks.append(w)
            hot.append(inside)
    side = g.at != "top"
    bw, _ = BOX[g.at]
    size = 78 if side else 112
    pad_x, pad_y = (30, 22) if side else (44, 30)
    lines = wrap(toks, size, bw - 2 * pad_x)
    while len(lines) > 3 and size > 44:
        size = int(size * 0.88)
        lines = wrap(toks, size, bw - 2 * pad_x)
    f = font(str(CAPTION_FONT), size)
    space, lh = f.getlength(" "), int(size * 1.14)
    widths = [sum(text_w(toks[i], size) for i in ln) + space * (len(ln) - 1) for ln in lines]
    w, h = int(max(widths) + 2 * pad_x), int(lh * len(lines) + 2 * pad_y - (lh - size) + 6)
    img = card(w, h, 26).copy()
    land, stay = presence(g, t)
    for li, (ln, lw) in enumerate(zip(lines, widths)):
        x = MARGIN + (w - lw) / 2
        for i in ln:
            k = out_cubic((t - g.t0 - 0.05 - 0.07 * i) / 0.16)
            if k > 0:
                col = (accent if hot[i] else INK) + (int(255 * k),)
                draw_text(img, x, MARGIN + pad_y + li * lh + (1 - k) * 16 - size * 0.06, toks[i], size, col)
            x += text_w(toks[i], size) + space
    return finish(img, min(out_cubic(land * 1.6), stay), 0.84 + 0.16 * out_back(land) - 0.06 * (1 - stay)), 0, 0


@lru_cache(maxsize=32)
def _picture(path: Path, w: int, h: int, mode: str = "fit") -> Image.Image:
    im = Image.open(path).convert("RGBA")
    s = min(w / im.width, h / im.height) if mode == "fit" else max(w / im.width, h / im.height)
    im = im.resize((max(1, int(im.width * s)), max(1, int(im.height * s))), Image.LANCZOS)
    if mode == "cover":
        x0, y0 = (im.width - w) // 2, (im.height - h) // 2
        im = im.crop((x0, y0, x0 + w, y0 + h))
    return im


def _icon(g: Graphic, t: float, accent: tuple, lang: str):
    side = g.at != "top"
    s = 220 if side else 300
    label = g.spec.get("label")
    lsize = 42 if side else 54
    lw = int(text_w(str(label), lsize, STICKER_FONT) + 56) if label else 0
    w = max(s, lw)
    h = s + (lsize + 34 + 22 if label else 0)
    img = Image.new("RGBA", (w + 2 * MARGIN, h + 2 * MARGIN))
    land, stay = presence(g, t)
    tile = card(s, s, int(0.26 * s)).copy()
    ic = g.spec["icon"]
    if isinstance(ic, Path):
        pic = _picture(ic, int(s * 0.68), int(s * 0.68))
        tile.alpha_composite(pic, (MARGIN + (s - pic.width) // 2, MARGIN + (s - pic.height) // 2))
    else:
        es = int(s * 0.56)
        draw_text(tile, MARGIN + (s - text_w(ic, es)) / 2, MARGIN + s * 0.2, ic, es, INK + (255,))
    k = 0.5 + 0.5 * out_back(land, 1.6)
    tile = tile.resize((max(1, int(tile.width * k)), max(1, int(tile.height * k))), Image.BILINEAR)
    tile = tile.rotate((1 - out_cubic(land)) * -14, resample=Image.BICUBIC)
    bob = 5 * math.sin(2 * math.pi * max(0.0, t - g.t0 - IN) / 2.6)
    cx = MARGIN + w / 2
    put(img, tile, cx - tile.width / 2, MARGIN + s / 2 - tile.height / 2 + bob)
    if label:
        kl = out_cubic((t - g.t0 - 0.14) / 0.22)
        if kl > 0:
            chip = card(lw, lsize + 34, (lsize + 34) // 2).copy()
            draw_text(chip, MARGIN + 28, MARGIN + 13, str(label), lsize, INK + (255,), STICKER_FONT)
            put(img, finish(chip, kl), cx - chip.width / 2, s + 22 + (1 - kl) * 14)
    return finish(img, min(out_cubic(land * 2), stay), 1 - 0.08 * (1 - stay)), 0, 0


def _digits(text: str, size: int) -> tuple[list[float], float]:
    """Fixed-width cells for digits, so a counting number doesn't wobble."""
    f = font(str(CAPTION_FONT), size)
    cell = max(f.getlength(d) for d in "0123456789")
    ws = [cell if c.isdigit() else f.getlength(c) for c in text]
    return ws, sum(ws)


def _count(g: Graphic, t: float, accent: tuple, lang: str):
    sp = g.spec
    side = g.at != "top"
    decimals = int(sp.get("decimals", 0))
    land, stay = presence(g, t)
    q = out_expo((t - g.t0 - 0.05) / float(sp.get("duration", 1.1)))
    val = sp["from"] + (sp["count"] - sp["from"]) * q
    final = f"{sp.get('prefix', '')}{number(sp['count'], decimals, lang)}{sp.get('suffix', '')}"
    shown = f"{sp.get('prefix', '')}{number(val, decimals, lang)}{sp.get('suffix', '')}"
    bw, _ = BOX[g.at]
    size = 156 if side else 230
    while _digits(final, size)[1] > bw - (60 if side else 40) and size > 40:
        size = int(size * 0.92)
    label = sp.get("label")
    lsize = 42 if side else 60
    llines = [" ".join(label.split()[i] for i in ln) for ln in wrap(label.split(), lsize, bw - 60, STICKER_FONT)] if label else []
    cells, nw = _digits(shown, size)
    w = int(max(_digits(final, size)[1], max((text_w(x, lsize, STICKER_FONT) for x in llines), default=0)) + (60 if side else 40))
    h = int(size * 1.02 + (len(llines) * lsize * 1.2 + 14 if llines else 0) + (36 if side else 10))
    img = (card(w, h, 28).copy() if side else Image.new("RGBA", (w + 2 * MARGIN, h + 2 * MARGIN)))
    ink = INK if side else PAPER
    sub = MUTED if side else (220, 228, 245)
    if not side:     # a soft shadow under the white number on the gradient
        sh = Image.new("RGBA", img.size)
        x = MARGIN + (w - nw) / 2
        for c, cw in zip(shown, cells):
            ImageDraw.Draw(sh).text((x + (cw - font(str(CAPTION_FONT), size).getlength(c)) / 2, MARGIN + 8 + 6), c,
                                    font=font(str(CAPTION_FONT), size), fill=(0, 0, 0, 120))
            x += cw
        img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(10)))
    d = ImageDraw.Draw(img)
    x = MARGIN + (w - nw) / 2
    f = font(str(CAPTION_FONT), size)
    for c, cw in zip(shown, cells):
        d.text((x + (cw - f.getlength(c)) / 2, MARGIN + (18 if side else 8) - size * 0.1), c, font=f,
               fill=ink + (255,))
        x += cw
    for li, ln in enumerate(llines):
        kl = out_cubic((t - g.t0 - 0.2) / 0.3)
        draw_text(img, MARGIN + (w - text_w(ln, lsize, STICKER_FONT)) / 2,
                  MARGIN + (18 if side else 8) + size * 1.02 + li * lsize * 1.2, ln, lsize, sub + (int(255 * kl),),
                  STICKER_FONT)
    return finish(img, min(out_cubic(land * 1.8), stay), 0.86 + 0.14 * out_back(land) - 0.06 * (1 - stay)), 0, 0


def _tick(d: ImageDraw.ImageDraw, cx: float, cy: float, r: float, accent: tuple) -> None:
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=accent + (255,))
    pts = [(cx - 0.42 * r, cy + 0.02 * r), (cx - 0.1 * r, cy + 0.34 * r), (cx + 0.46 * r, cy - 0.32 * r)]
    d.line(pts, fill=PAPER + (255,), width=max(3, int(r * 0.26)), joint="curve")


def _list(g: Graphic, t: float, accent: tuple, lang: str):
    items = g.spec["list"]
    side = g.at != "top"
    bw, _ = BOX[g.at]
    size = 58 if side else 68
    while max(text_w(x, size, STICKER_FONT) for x in items) > bw - size * 2.4 and size > 30:
        size = int(size * 0.92)
    ch, gap, r = int(size * 1.7), 16, size * 0.42
    widths = [int(text_w(x, size, STICKER_FONT) + size * 2.3) for x in items]
    w, h = max(widths), len(items) * ch + (len(items) - 1) * gap
    img = Image.new("RGBA", (w + 2 * MARGIN, h + 2 * MARGIN))
    _, stay = presence(g, t)
    for i, (it, iw) in enumerate(zip(items, widths)):
        k = out_cubic((t - g.beats[i]) / 0.26)
        if k <= 0:
            continue
        chip = card(iw, ch, ch // 2).copy()
        d = ImageDraw.Draw(chip)
        _tick(d, MARGIN + ch / 2, MARGIN + ch / 2, r, accent)
        draw_text(chip, MARGIN + ch / 2 + r + size * 0.4, MARGIN + (ch - size) / 2 - size * 0.08, it, size,
                  INK + (255,), STICKER_FONT)
        layer = finish(chip, k, 0.8 + 0.2 * out_back((t - g.beats[i]) / 0.26))
        if layer is not None:   # scaled about the chip's centre, sliding in from the right
            cx, cy = MARGIN + iw / 2 + (1 - k) * 40, MARGIN + i * (ch + gap) + ch / 2
            put(img, layer, cx - layer.shape[1] / 2, cy - layer.shape[0] / 2)
    return finish(img, stay), 0, 0


def _bars(g: Graphic, t: float, accent: tuple, lang: str):
    sp = g.spec
    vals = [float(v) for v in sp["bars"]]
    labels = [str(x) for x in sp.get("labels") or []]
    shown = [str(x) for x in sp.get("values") or []]     # what each bar says, e.g. "<5%"; else its value
    title = sp.get("title")
    w, h = 920, 600
    img = card(w, h, 32).copy()
    d = ImageDraw.Draw(img)
    land, stay = presence(g, t)
    top = MARGIN + 44
    if title:
        draw_text(img, MARGIN + 48, top, str(title), 48, INK + (255,), STICKER_FONT)
        top += 84
    lab_h = 60 if labels else 16
    base = MARGIN + h - 36 - lab_h
    area_top = top + 60
    n = len(vals)
    gap = 28
    bwid = (w - 96 - gap * (n - 1)) / n
    vmax = max(vals) or 1
    for i, v in enumerate(vals):
        k = out_cubic((t - g.t0 - 0.12 - 0.12 * i) / 0.7)
        x0 = MARGIN + 48 + i * (bwid + gap)
        full = (base - area_top) * v / vmax
        bh = max(0.0, full * k)
        col = accent if i == n - 1 else mix(accent, 0.28)
        if bh > 2:
            d.rounded_rectangle([x0, base - bh, x0 + bwid, base], radius=min(16, int(bh / 2)), fill=col + (255,))
        if k > 0.75:
            vs = shown[i] if shown else compact(v, lang)
            a = int(255 * clip((k - 0.75) / 0.25))
            draw_text(img, x0 + (bwid - text_w(vs, 42, STICKER_FONT)) / 2, base - bh - 58, vs, 42,
                      (INK if i == n - 1 else MUTED) + (a,), STICKER_FONT)
        if labels:
            draw_text(img, x0 + (bwid - text_w(labels[i], 38, STICKER_FONT)) / 2, base + 16, labels[i], 38,
                      MUTED + (255,), STICKER_FONT)
    return finish(img, min(out_cubic(land * 1.8), stay), 0.9 + 0.1 * out_cubic(land) - 0.05 * (1 - stay)), 0, 0


def _image(g: Graphic, t: float, accent: tuple, lang: str):
    sp = g.spec
    path: Path = sp["image"]
    bw, bh = BOX[g.at]
    land, stay = presence(g, t)
    if path.suffix.lower() in VIDEO:     # B-roll: its frames play while the card is up
        st = g.state
        if "size" not in st:
            vw, vh = media.size(path)
            s = min(bw / vw, (bh - 40) / vh)
            st["size"] = (int(vw * s) // 2 * 2, int(vh * s) // 2 * 2)
            n = int((g.t1 - g.t0) * media.FPS) + 2
            st["frames"] = media.frames(path, float(sp.get("start", 0.0)), n, *st["size"])
        fw, fh = st["size"]
        f = next(st["frames"], None)
        st["last"] = f if f is not None else st.get("last")
        pic = Image.fromarray(st["last"][..., ::-1]).convert("RGBA") if st["last"] is not None else Image.new("RGBA", (fw, fh))
    else:
        base = _picture(path, bw, bh - 40)
        z = 1 + 0.045 * clip((t - g.t0) / max(0.5, g.t1 - g.t0))     # a slow push-in on a still
        if z > 1.001:
            cw, chh = int(base.width / z), int(base.height / z)
            x0, y0 = (base.width - cw) // 2, (base.height - chh) // 2
            pic = base.crop((x0, y0, x0 + cw, y0 + chh)).resize(base.size, Image.BILINEAR)
        else:
            pic = base
    w, h = pic.size
    r = 28 if g.at == "top" else 22
    img = card(w, h, r).copy()
    mask = Image.new("L", (w, h))
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius=r, fill=255)
    img.paste(pic.convert("RGB"), (MARGIN, MARGIN), mask)
    cap = sp.get("caption")
    if cap:
        cs = 40 if g.at == "top" else 32
        cw = int(text_w(str(cap), cs, STICKER_FONT) + 48)
        chip = card(cw, cs + 30, (cs + 30) // 2)
        kc = out_cubic((t - g.t0 - 0.2) / 0.25)
        if kc > 0:
            c = chip.copy()
            draw_text(c, MARGIN + 24, MARGIN + 11, str(cap), cs, INK + (255,), STICKER_FONT)
            put(img, finish(c, kc), 24, h - (cs + 30) / 2)
    k = out_cubic(land)
    dx = int((1 - k) * (190 if g.at != "left" else -190))
    return finish(img, min(out_cubic(land * 1.4), stay), 1 - 0.04 * (1 - stay)), dx, 0


def _chat(g: Graphic, t: float, accent: tuple, lang: str):
    sp = g.spec
    msgs = sp["chat"]
    w = 900
    size, pad_x, pad_y, gap = 44, 30, 20, 16
    max_bw = int(w * 0.74)
    title = sp.get("title")
    head = 96 if title else 0
    laid = []
    for m in msgs:
        ws = m["text"].split()
        lines = [" ".join(ws[i] for i in ln) for ln in wrap(ws, size, max_bw - 2 * pad_x, STICKER_FONT)]
        bwid = int(max(text_w(x, size, STICKER_FONT) for x in lines) + 2 * pad_x)
        bh = int(len(lines) * size * 1.25 + 2 * pad_y - size * 0.25 + 4)
        laid.append((m, lines, bwid, bh))
    h = head + 40 + sum(b[3] for b in laid) + gap * (len(laid) - 1) + 40
    img = card(w, h, 34).copy()
    d = ImageDraw.Draw(img)
    land, stay = presence(g, t)
    if title:
        cx, cy = MARGIN + 40 + 30, MARGIN + 28 + 30
        d.ellipse([cx - 30, cy - 30, cx + 30, cy + 30], fill=accent + (255,))
        ini = str(title)[:1].upper()
        draw_text(img, cx - text_w(ini, 34, STICKER_FONT) / 2, cy - 22, ini, 34, PAPER + (255,), STICKER_FONT)
        draw_text(img, cx + 46, cy - 24, str(title), 40, INK + (255,), STICKER_FONT)
        d.line([(MARGIN + 30, MARGIN + head), (MARGIN + w - 30, MARGIN + head)], fill=SOFT + (255,), width=3)
    y = MARGIN + head + 40
    for i, (m, lines, bwid, bh) in enumerate(laid):
        k = (t - g.beats[i]) / 0.28
        if k > 0:
            out = m["side"] == "out"
            bub = Image.new("RGBA", (bwid, bh))
            bd = ImageDraw.Draw(bub)
            bd.rounded_rectangle([0, 0, bwid - 1, bh - 1], radius=28, fill=(accent if out else SOFT) + (255,))
            for li, ln in enumerate(lines):
                draw_text(bub, pad_x, pad_y + li * size * 1.25 - size * 0.08, ln, size,
                          (PAPER if out else INK) + (255,), STICKER_FONT)
            sc = 0.7 + 0.3 * out_back(k, 1.4)
            layer = finish(bub, out_cubic(k * 1.5), sc)
            if layer is not None:
                lw, lh = layer.shape[1], layer.shape[0]
                put(img, layer, MARGIN + (w - 36 - lw if out else 36), y + (bh - lh) / 2 + (1 - out_cubic(k)) * 12)
        y += bh + gap
    return finish(img, min(out_cubic(land * 1.8), stay), 0.9 + 0.1 * out_back(land) - 0.05 * (1 - stay)), 0, 0


DRAW = {"text": _text, "icon": _icon, "count": _count, "list": _list, "bars": _bars, "image": _image, "chat": _chat}
