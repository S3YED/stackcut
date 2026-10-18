"""Word-by-word captions, the hook sticker and the progress bar, drawn with Pillow.

Every text layer is an RGBA numpy array blended onto the frame, so no libass or drawtext is needed."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .speech import Word

FONTS = Path(__file__).parent / "assets" / "fonts"
CAPTION_FONT = FONTS / "Geist-ExtraBold.ttf"
STICKER_FONT = FONTS / "Geist-Bold.ttf"
POP = 0.12


@dataclass
class Page:
    words: list[Word]
    start: float
    end: float


def display(word: str) -> str:
    """Drop trailing commas and full stops; questions keep their mark."""
    return re.sub(r"[,.;:]+$", "", word)


def paginate(words: list[Word], max_words: int = 3, max_chars: int = 16) -> list[Page]:
    pages, cur = [], []
    for w in words:
        cur.append(w)
        chars = sum(len(x.text) + 1 for x in cur)
        if (len(cur) >= max_words or chars >= max_chars or re.search(r"[.?!]$", w.text)
                or (w.text.endswith(",") and len(cur) >= 2)):
            pages.append(cur)
            cur = []
    if cur:
        pages.append(cur)
    out = []
    for i, p in enumerate(pages):
        nxt = pages[i + 1][0].start if i + 1 < len(pages) else None
        end = p[-1].end + 0.3
        if nxt is not None and nxt - p[-1].end < 0.6:
            end = nxt
        out.append(Page(p, p[0].start, end))
    return out


@lru_cache(maxsize=None)
def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


STOPWORDS = set("""a an the and or but so to of in on at by for with from into onto up out is are was were be
been being am it its it's this that these those there here then than i i'm i'll i've me my we we're our you
you're your yours he he's him his she her they them their do does did done can could will would should may
might must have has had not no nor as if when what which who whom how why where all any some just very really
also too one two get gets got let let's go going de het een en van ik je mijn is dat die er""".split())


def keyword(tokens: tuple[str, ...]) -> int:
    """The word that carries the meaning: numbers, names and long content words beat filler."""
    def score(i: int) -> float:
        w = tokens[i].strip("?\"'").lower()
        if w in STOPWORDS or not w:
            return -100
        raw = tokens[i].strip("?\"'")
        return len(w) + (6 if any(c.isdigit() for c in w) else 0) + (4 if raw[:1].isupper() and i > 0 else 0)
    return max(range(len(tokens)), key=score)


@lru_cache(maxsize=4096)
def render_page(tokens: tuple[str, ...], active: int, accent: tuple[int, int, int], size: int = 70,
                max_w: int = 960) -> np.ndarray:
    """Spoken words light up, unspoken ones wait dimmed, and the page's keyword gets the accent pill."""
    f = font(str(CAPTION_FONT), size)
    space = int(f.getlength(" "))
    lines, cur, cur_w = [], [], 0
    for i, t in enumerate(tokens):
        tw = f.getlength(t)
        if cur and cur_w + space + tw > max_w:
            lines.append(cur)
            cur, cur_w = [], 0
        cur_w += (space if cur else 0) + tw
        cur.append(i)
    lines.append(cur)
    pad_x, pad_y, line_h = 18, 10, int(size * 1.28)
    widths = [sum(f.getlength(tokens[i]) for i in l) + space * (len(l) - 1) for l in lines]
    bw, bh = int(max(widths)) + 2 * pad_x + 24, line_h * len(lines) + 2 * pad_y + 24
    img, shadow = Image.new("RGBA", (bw, bh)), Image.new("RGBA", (bw, bh))
    d, sd = ImageDraw.Draw(img), ImageDraw.Draw(shadow)
    asc, _ = f.getmetrics()
    key = keyword(tokens)
    for li, (line, lw) in enumerate(zip(lines, widths)):
        x, y = (bw - lw) / 2, 12 + pad_y + li * line_h
        for i in line:
            tw = f.getlength(tokens[i])
            pill = i == key and i <= active
            alpha = 255 if i <= active else 105
            if pill:
                d.rounded_rectangle([x - pad_x, y - pad_y + 4, x + tw + pad_x, y + asc + pad_y + 2], radius=16,
                                    fill=accent + (255,))
            sd.text((x, y + 4), tokens[i], font=f, fill=(0, 0, 0, int(170 * alpha / 255)))
            d.text((x, y), tokens[i], font=f, fill=(255, 255, 255, alpha),
                   stroke_width=0 if pill else 5, stroke_fill=(0, 0, 0, int(235 * alpha / 255)))
            x += tw + space
    return np.asarray(Image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(6)), img))


def caption_at(pages: list[Page], t: float, accent: tuple[int, int, int]) -> np.ndarray | None:
    for p in pages:
        if p.start <= t < p.end:
            active = max((i for i, w in enumerate(p.words) if w.start <= t), default=0)
            img = render_page(tuple(display(w.text) for w in p.words), active, accent)
            k = min(1.0, (t - p.start) / POP)
            s = 0.86 + 0.14 * (1 - (1 - k) ** 3)
            if s < 0.999:
                h, w = img.shape[:2]
                img = np.asarray(Image.fromarray(img).resize((max(1, int(w * s)), max(1, int(h * s))),
                                                             Image.BILINEAR))
            return img
    return None


EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]+")
EMOJI_FONTS = (("/System/Library/Fonts/Apple Color Emoji.ttc", (20, 26, 32, 40, 48, 52, 64, 96, 160)),
               ("/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf", (109,)),
               ("/usr/share/fonts/noto/NotoColorEmoji.ttf", (109,)))


def _emoji_font(size: int):
    for path, sizes in EMOJI_FONTS:
        if os.path.exists(path):
            fits = [z for z in sizes if z <= size]
            return ImageFont.truetype(path, max(fits) if fits else min(sizes)), size / (max(fits) if fits else min(sizes))
    return None, 1.0


@lru_cache(maxsize=16)
def sticker(text: str, size: int = 64, max_w: int = 960, ink: tuple[int, int, int] = (8, 11, 18)) -> np.ndarray:
    """The hook: ink on a white rounded box, the native text-sticker look of Reels and TikTok.
    One line; it shrinks to fit rather than wrap (write hooks of about 28 characters)."""
    f = font(str(STICKER_FONT), size)
    ef, escale = _emoji_font(size)
    if ef is None:
        text = EMOJI.sub("", text).strip()
    runs, pos = [], 0
    for m in EMOJI.finditer(text) if ef else []:
        if m.start() > pos:
            runs.append((text[pos:m.start()], False))
        runs.append((m.group(), True))
        pos = m.end()
    if pos < len(text):
        runs.append((text[pos:], False))
    width = sum(ef.getlength(t) * escale if e else f.getlength(t) for t, e in runs)
    if width > max_w - 60 and size > 40:
        return sticker(text, int(size * (max_w - 60) / width), max_w, ink)
    asc, desc = f.getmetrics()
    pad_x, pad_y = 26, 14
    bw, bh = int(width) + 2 * pad_x + 4, asc + desc + 2 * pad_y + 4
    img = Image.new("RGBA", (bw, bh))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([2, 2, bw - 2, bh - 2], radius=18, fill=(255, 255, 255, 255))
    x, y = pad_x + 2, pad_y + 2
    for t, is_emoji in runs:
        if is_emoji:
            e = Image.new("RGBA", (int(ef.getlength(t)) + 4, ef.size + 20))
            ImageDraw.Draw(e).text((0, 0), t, font=ef, embedded_color=True)
            e = e.resize((max(1, int(e.width * escale)), max(1, int(e.height * escale))), Image.LANCZOS)
            img.alpha_composite(e, (int(x), int(y + (asc - size) * 0.6)))
            x += ef.getlength(t) * escale
        else:
            d.text((x, y), t, font=f, fill=ink + (255,))
            x += f.getlength(t)
    return np.asarray(img)


def blend(dst: np.ndarray, rgba: np.ndarray, cx: int, cy: int, opacity: float = 1.0) -> None:
    """Alpha-blend an RGBA layer (RGB order) onto a BGR frame, centred on (cx, cy), clipped."""
    h, w = rgba.shape[:2]
    x0, y0 = cx - w // 2, cy - h // 2
    sx0, sy0 = max(0, -x0), max(0, -y0)
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(dst.shape[1], cx - w // 2 + w), min(dst.shape[0], cy - h // 2 + h)
    if x1 <= x0 or y1 <= y0:
        return
    src = rgba[sy0:sy0 + (y1 - y0), sx0:sx0 + (x1 - x0)]
    a = src[..., 3:4].astype(np.float32) * (opacity / 255.0)
    region = dst[y0:y1, x0:x1]
    region[:] = (src[..., 2::-1] * a + region * (1 - a)).astype(np.uint8)


def progress(dst: np.ndarray, frac: float, accent_bgr: tuple[int, int, int], y: int = 150, margin: int = 60) -> None:
    x0, bw = margin, dst.shape[1] - 2 * margin
    dst[y:y + 6, x0:x0 + bw] = (dst[y:y + 6, x0:x0 + bw] * 0.6 + 255 * 0.4).astype(np.uint8)
    dst[y:y + 6, x0:x0 + int(bw * min(1.0, frac))] = accent_bgr
