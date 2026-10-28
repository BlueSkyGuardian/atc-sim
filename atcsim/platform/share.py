"""Link previews: the picture WhatsApp, LinkedIn, Facebook, X, Slack... show when a news link is shared.

A 1200 x 630 JPEG (the size every one of them takes, well under WhatsApp's 300 kB): on the left, on the site's dark
blue, the region, the date and the title in the site's type (Archivo); on the right the article's first figure, whole;
without a figure, a radar scope's range rings. The news page itself has one too (`site_card`)."""
from __future__ import annotations

import os
from typing import Optional

W, H = 1200, 630
BG = (13, 27, 42)            # --bg
TEXT = (238, 243, 247)
DIM = (160, 179, 197)
MAGENTA = (209, 71, 143)
_FONT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "vendor", "fonts", "archivo-latin-wdth-normal.woff2")
_FALLBACKS = ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "DejaVuSans-Bold.ttf")


def _font(size: int, weight: int = 700, width: int = 100):
    from PIL import ImageFont
    try:
        f = ImageFont.truetype(_FONT, size)
        try:
            f.set_variation_by_axes([weight, width])
        except Exception:                                 # noqa: BLE001 - a FreeType without variations: the default weight
            pass
        return f
    except Exception:                                     # noqa: BLE001
        for p in _FALLBACKS:
            try:
                return ImageFont.truetype(p, size)
            except Exception:                             # noqa: BLE001
                continue
        return ImageFont.load_default(size)


def _wrap(draw, text: str, font, width: int, max_lines: int):
    """The lines of `text` in `width` pixels; None when it takes more than max_lines."""
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=font) <= width:
            cur = t
            continue
        if cur:
            lines.append(cur)
        cur = w
        if len(lines) >= max_lines:
            return None
    if cur:
        lines.append(cur)
    return lines if len(lines) <= max_lines else None


def _title_lines(draw, title: str, width: int, max_lines: int = 5):
    for size in (54, 50, 46, 42, 38, 34):
        f = _font(size, 760, 92)
        lines = _wrap(draw, title, f, width, max_lines)
        if lines:
            return f, size, lines
    f = _font(34, 760, 92)
    words, lines, cur = title.split(), [], ""
    for w in words:                                       # still too long: the lines that fit, then an ellipsis
        t = (cur + " " + w).strip()
        if draw.textlength(t + " …", font=f) <= width:
            cur = t
        else:
            lines.append(cur)
            cur = w
            if len(lines) == max_lines:
                break
    lines = (lines + [cur])[:max_lines]
    lines[-1] = lines[-1].rstrip(".,;:") + " …"
    return f, 34, lines


def _figure(png_path: str, w_box: int, h_box: int):
    """The figure, its own title strip and source line cut off, fitted whole in the box (on white)."""
    from PIL import Image
    im = Image.open(png_path).convert("RGB")
    w, h = im.size
    im = im.crop((0, int(h * 0.12), w, int(h * 0.955)))           # gfx.py figures: title on top, source at the bottom
    w, h = im.size
    s = min(w_box / w, h_box / h)
    im = im.resize((max(1, round(w * s)), max(1, round(h * s))), Image.LANCZOS)
    box = Image.new("RGB", (w_box, h_box), (255, 255, 255))
    box.paste(im, ((w_box - im.size[0]) // 2, (h_box - im.size[1]) // 2))
    return box


def _rings(img, cx: int, cy: int):
    """The plain card's decoration: a radar scope's range rings and a few targets."""
    from PIL import ImageDraw
    d = ImageDraw.Draw(img)
    for r in range(70, 520, 70):
        d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(30, 52, 75), width=2)
    d.line((cx - 500, cy, cx + 500, cy), fill=(30, 52, 75), width=2)
    d.line((cx, cy - 500, cx, cy + 500), fill=(30, 52, 75), width=2)
    for dx, dy in ((-150, -95), (120, -200), (210, 60), (-60, 170), (330, -40)):
        x, y = cx + dx, cy + dy
        d.rectangle((x - 6, y - 6, x + 6, y + 6), outline=(95, 211, 163), width=3)
        d.line((x + 6, y - 6, x + 30, y - 30), fill=(95, 211, 163), width=2)


def card(out: str, title: str, kicker: str = "", figure_png: Optional[str] = None, brand: str = "ATC SIM · Aviation news") -> str:
    """Draw the card into `out` (a .jpg) and return its path: the text on the left, the figure on the right."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (W, H), BG)
    fig = None
    if figure_png and os.path.exists(figure_png):
        try:
            fig = _figure(figure_png, 600, H)
        except Exception:                                 # noqa: BLE001 - a figure Pillow cannot read: the plain card
            fig = None
    if fig is not None:
        img.paste(fig, (W - 600, 0))
        tw = W - 600 - 2 * 56
        ImageDraw.Draw(img).rectangle((W - 600 - 6, 0, W - 600 - 1, H), fill=MAGENTA)
    else:
        _rings(img, W - 230, H // 2)
        tw = W - 2 * 56 - 260
    d = ImageDraw.Draw(img)
    pad = 56
    bf = _font(26, 700, 100)
    d.text((pad, 56), brand, font=bf, fill=TEXT, anchor="lm")
    tf, ts, lines = _title_lines(d, title, tw)
    lh = int(ts * 1.15)
    block = (30 + 22 if kicker else 0) + lh * len(lines)
    y = max(110, (H - block) // 2 + 20)
    if kicker:
        kf = _font(23, 700, 100)
        d.rectangle((pad, y + 2, pad + 6, y + 28), fill=MAGENTA)
        d.text((pad + 18, y + 15), kicker.upper(), font=kf, fill=(232, 120, 178), anchor="lm")
        y += 30 + 22
    for ln in lines:
        d.text((pad, y), ln, font=tf, fill=TEXT)
        y += lh
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    tmp = out + ".tmp"
    img.save(tmp, "JPEG", quality=86, optimize=True, progressive=True)
    os.replace(tmp, out)
    return out


def site_card(out: str) -> str:
    return card(out, "Aviation news for air traffic controllers", "Morocco · Europe · International", None)
