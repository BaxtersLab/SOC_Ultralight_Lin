#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate the Timer Prompt clock icon at every size the shell asks for.

Timer Prompt showed a generic gear on the taskbar because it set no window
icon at all. A clock is the obvious mark, but a literal clock face collapses
into mush at 48px -- the size the dash actually renders -- so the detail is
budgeted deliberately:

  * a thick ring, not a thin outline (survives downscaling)
  * FOUR tick marks, not twelve (twelve become a grey smear)
  * two hands at 10:10, the classic reading angle, with a clear length
    difference so they stay distinguishable
  * no numerals at all

Palette is Timer Prompt's own (timer_prompt.py:73-80).
"""
from pathlib import Path
from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent / "assets"
OUT.mkdir(exist_ok=True)

BG_OUTER = (22, 22, 24)       # plate
BG_INNER = (45, 45, 45)       # BG2 -- clock face
ACCENT   = (86, 156, 214)     # ACCENT -- ring + hour hand
YELLOW   = (220, 220, 170)    # YELLOW -- minute hand
DIM      = (138, 138, 138)    # DIM -- tick marks

# Render at 4x and downsample: Pillow has no antialiased arc/line, and at 48px
# a jagged ring is exactly what makes an icon look broken.
SS = 4

def draw_icon(S):
    W = S * SS
    img = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = int(W * 0.03)
    r = int(W * 0.20)

    d.rounded_rectangle([pad, pad, W-pad, W-pad], radius=r, fill=BG_OUTER)

    cx = cy = W / 2
    face = W * 0.355
    ring = max(SS, int(W * 0.055))
    d.ellipse([cx-face, cy-face, cx+face, cy+face], fill=BG_INNER,
              outline=ACCENT, width=ring)

    # four ticks only
    tick_len, tick_w = face * 0.20, max(SS, int(W * 0.035))
    for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
        x0 = cx + dx * (face - ring * 0.6)
        y0 = cy + dy * (face - ring * 0.6)
        x1 = cx + dx * (face - ring * 0.6 - tick_len)
        y1 = cy + dy * (face - ring * 0.6 - tick_len)
        d.line([(x0, y0), (x1, y1)], fill=DIM, width=tick_w)

    # hands at 10:10 -- hour short and thick, minute long and thin
    hand_w = max(SS, int(W * 0.050))
    d.line([(cx, cy), (cx - face * 0.40, cy - face * 0.30)],
           fill=ACCENT, width=hand_w)                       # hour, to 10
    d.line([(cx, cy), (cx + face * 0.34, cy - face * 0.52)],
           fill=YELLOW, width=max(SS, int(W * 0.038)))      # minute, to 2
    hub = max(SS, int(W * 0.035))
    d.ellipse([cx-hub, cy-hub, cx+hub, cy+hub], fill=YELLOW)

    return img.resize((S, S), Image.LANCZOS)

for S in (256, 128, 64, 48, 32, 24, 16):
    draw_icon(S).save(OUT / f"timer-prompt-{S}.png")
draw_icon(256).save(OUT / "timer-prompt.png")
print("wrote:", ", ".join(sorted(p.name for p in OUT.glob("timer-prompt*.png"))))
