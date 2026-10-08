#!/usr/bin/env python3
"""Render a 132×64, one-bit NightBlood-inspired face for offline T31G review.

The almond-eye geometry and state grammar are adapted from NightBlood Remote
commit 4824aceb1cd04ac521f7f0342771c20edad60dac (MIT); see
docs/third-party/NightBloodRemote-LICENSE.txt. This is not a phone transport or
a claim that the T31G can display these PNGs directly.
"""

from __future__ import annotations

import argparse
import math
import struct
import zlib
from pathlib import Path

WIDTH = 132
HEIGHT = 64
FRAMES = 8
STATES = ("idle", "listening", "thinking", "speaking", "completed", "error")
FONT_5X7 = {
    " ": ("00000",) * 7,
    "%": ("11001", "11010", "00100", "00100", "01011", "10011", "00000"),
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "10000", "11110", "00001", "00001", "11110"),
    "6": ("01111", "10000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "W": ("10001", "10001", "10001", "10101", "10101", "10101", "01010"),
}


def draw_text(pixels: bytearray, label: str, x: int, y: int) -> None:
    for char in label:
        for row, bits in enumerate(FONT_5X7[char]):
            for col, bit in enumerate(bits):
                if bit == "1":
                    pixels[(y + row) * WIDTH + x + col] = 255
        x += 6


def draw_week_meter(pixels: bytearray, remaining: int) -> None:
    """Place the weekly quota in a quiet row of cells along the bottom edge."""
    if not 0 <= remaining <= 100:
        raise ValueError("week_remaining must be 0..100")
    draw_text(pixels, "WEEK", 2, 49)
    percent = f"{remaining}%"
    draw_text(pixels, percent, WIDTH - 2 - (len(percent) * 6 - 1), 55)
    cell_count = 26
    filled = 0 if remaining == 0 else max(1, round(cell_count * remaining / 100))
    for cell in range(cell_count):
        left = 2 + 4 * cell
        if cell < filled:
            for y in range(60, 63):
                for x in range(left, left + 3):
                    pixels[y * WIDTH + x] = 255
        else:
            for y in (60, 62):
                for x in (left, left + 2):
                    pixels[y * WIDTH + x] = 255


def draw_eye(pixels: bytearray, centre_x: float, centre_y: float,
             aperture: float, squint: float, shift_x: float = 0) -> None:
    """Port the source's almond-lid curve to a larger, binary T31G silhouette."""
    half_width = 20.0
    for y in range(HEIGHT):
        for x in range(WIDTH):
            u = (x + 0.5 - centre_x - shift_x) / half_width
            if abs(u) >= 1:
                continue
            arch = max(0.0, 1.0 - u * u)
            top = (0.42 - 0.15 * squint) * arch ** (1.35 + 0.45 * squint)
            bottom = (0.26 - 0.10 * squint) * arch ** 1.25
            vertical = (centre_y - (y + 0.5)) / (half_width * max(aperture, 0.045))
            if -bottom <= vertical <= top:
                pixels[y * WIDTH + x] = 255


def draw_voice(pixels: bytearray, phase: int) -> None:
    """A one-bit, low-height speech ribbon; it is illustrative, not live audio."""
    levels = (0.16, 0.38, 0.76, 1.0, 0.60, 0.27, 0.86, 0.40)
    level = levels[phase % FRAMES]
    for x in range(39, 95, 3):
        u = (x - 66) / 28.0
        taper = max(0.0, 1.0 - u * u) ** 1.3
        jag = 0.25 + 0.75 * abs(math.sin(x * 0.47 + phase * 1.7))
        height = max(1, round(6.0 * level * taper * jag))
        for y in range(49 - height, 50 + height):
            if 0 <= y < HEIGHT:
                pixels[y * WIDTH + x] = 255


def render(state: str, phase: int, week_remaining: int = 8,
           inverted: bool = False) -> bytearray:
    if state not in STATES:
        raise ValueError(f"unknown state: {state}")
    if not 0 <= phase < FRAMES:
        raise ValueError(f"phase must be 0..{FRAMES - 1}")
    pixels = bytearray(WIDTH * HEIGHT)
    settings = {
        "idle": (0.68, 0.0, 0.0),
        "listening": (0.90, 0.0, 0.0),
        "thinking": (0.40, 0.68, 0.0),
        "speaking": (0.78, 0.0, 0.0),
        "completed": (0.74, 0.0, 0.0),
        "error": (0.60, 0.0, 0.0),
    }
    aperture, squint, shift = settings[state]
    left_aperture = right_aperture = aperture
    if state == "idle":
        # The monochrome LCD hides one-pixel breathing/gaze changes. Use a
        # deliberately legible alternating wink sequence instead: left closes,
        # both open wide, then right closes.
        left_aperture = (0.78, 0.62, 0.32, 0.07, 0.84, 0.84, 0.84, 0.84)[phase]
        right_aperture = (0.78, 0.78, 0.78, 0.78, 0.84, 0.62, 0.32, 0.07)[phase]
    elif state == "listening":
        # Make the connected/listening state legible on the slow monochrome
        # LCD: a broad breathing cycle survives the handset's redraw cadence.
        aperture = (0.66, 0.80, 0.96, 1.06, 0.96, 0.80, 0.66, 0.80)[phase]
    elif state == "thinking":
        aperture += (0.06 if phase % 2 else 0.0)
    elif state == "completed" and phase < 3:
        aperture += 0.10
    elif state == "error":
        aperture = 0.15 if phase % 2 else 0.60
    if state == "thinking":
        shift = -2.0  # held, unfocused glance
    elif state == "listening":
        shift = (-2.0, -1.0, 0.0, 1.0, 2.0, 1.0, 0.0, -1.0)[phase]
    if state != "idle":
        left_aperture = right_aperture = aperture
    draw_eye(pixels, 37, 28, left_aperture, squint, shift)
    draw_eye(pixels, 95, 28, right_aperture, squint, shift)
    if left_aperture <= 0.07:
        for x in range(25, 50):
            pixels[28 * WIDTH + x] = 255
    if right_aperture <= 0.07:
        for x in range(83, 108):
            pixels[28 * WIDTH + x] = 255
    if state == "speaking":
        draw_voice(pixels, phase)
    if state == "thinking":
        # Minimal progress cue in lieu of the source's colour and glow.
        for x in range(62, 62 + 2 * (phase % 4 + 1)):
            pixels[53 * WIDTH + x] = 255
    draw_week_meter(pixels, week_remaining)
    if inverted:
        return bytearray(255 - pixel for pixel in pixels)
    return pixels


def png_bytes(pixels: bytes | bytearray) -> bytes:
    if len(pixels) != WIDTH * HEIGHT:
        raise ValueError("image size must be 132×64")

    def chunk(name: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + name + data + struct.pack(
            ">I", zlib.crc32(name + data) & 0xFFFFFFFF
        )

    rows = b"".join(b"\x00" + pixels[y * WIDTH:(y + 1) * WIDTH]
                    for y in range(HEIGHT))
    header = struct.pack(">IIBBBBB", WIDTH, HEIGHT, 8, 0, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def preview_html(week_remaining: int) -> str:
    options = "".join(f'<button data-state="{state}">{state}</button>' for state in STATES)
    return f"""<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>DialAgent · T31G Face 132×64</title>
<style>
body{{background:#17191d;color:#eef0f3;font:16px system-ui;margin:36px auto;max-width:760px;padding:0 20px}}
h1{{font-size:22px}} p{{line-height:1.5;color:#bcc2cd}}
.phone{{display:inline-block;background:#30343a;border:12px solid #41454c;border-radius:20px;padding:13px;box-shadow:0 18px 35px #0007}}
img{{display:block;width:528px;height:256px;max-width:100%;image-rendering:pixelated;background:#000}}
.controls{{display:flex;flex-wrap:wrap;gap:8px;margin:20px 0}}
button{{background:#303740;border:1px solid #5f6975;color:white;border-radius:8px;padding:8px 12px;cursor:pointer}}
button.active{{background:#c4d8f8;color:#111}}
small{{color:#9ca5b1}}
</style>
<h1>DialAgent · T31G 单色表情预览</h1>
<p>132×64 原生像素，放大 4 倍；每秒一帧。底边 WEEK {week_remaining}% 是可替换的预览值，尚未接通实时用量，也不是话机屏幕实测。</p>
<p id="live-status">手动预览。通过本机预览服务打开后，可跟随 SIP 接听器状态。</p>
<div class="phone"><img id="face" width="132" height="64" alt="132×64 face animation"></div>
<div class="controls" id="states">{options}</div>
<div class="controls" id="themes"><button data-theme="dark">黑底白画</button><button data-theme="light">白底黑画</button></div>
<small>借鉴 NightBlood Remote 的杏仁形双眼与会话状态；已删除 WebGL、颜色、烟雾和高帧率效果。</small>
<script>
const image=document.querySelector('#face');
const stateButtons=[...document.querySelectorAll('#states button')];
const themeButtons=[...document.querySelectorAll('#themes button')];
let state='idle', theme='dark', frame=0;
let manual=false;
function draw(){{
  const suffix=theme==='light' ? '-light' : '';
  image.src=`${{state}}-${{String(frame).padStart(2,'0')}}${{suffix}}.png?v=bottom-rail-1`;
  frame=(frame+1)%{FRAMES};
}}
stateButtons.forEach(button=>button.addEventListener('click',()=>{{manual=true;state=button.dataset.state;frame=0;
  stateButtons.forEach(b=>b.classList.toggle('active',b===button));draw();}}));
themeButtons.forEach(button=>button.addEventListener('click',()=>{{theme=button.dataset.theme;frame=0;
  themeButtons.forEach(b=>b.classList.toggle('active',b===button));draw();}}));
stateButtons[0].classList.add('active');themeButtons[0].classList.add('active');draw();setInterval(draw,1000);
if(location.protocol==='http:')setInterval(async()=>{{
  if(manual)return;
  try{{
    const response=await fetch('/state',{{cache:'no-store'}});
    if(!response.ok)throw new Error('无状态');
    const live=await response.json();
    const age=Date.now()-Date.parse(live.updated_at);
    if(!Number.isFinite(age)||age>15000)throw new Error('状态已过期');
    if(!{list(STATES)!r}.includes(live.state))throw new Error('未知状态');
    if(state!==live.state){{state=live.state;frame=0;stateButtons.forEach(b=>b.classList.toggle('active',b.dataset.state===state));draw();}}
    document.querySelector('#live-status').textContent=`本机联动：${{live.state}} · ${{live.reason}} · ${{live.certainty}}（不是 Codex 内部状态）`;
  }}catch(error){{
    if(state!=='error'){{state='error';frame=0;stateButtons.forEach(b=>b.classList.toggle('active',b.dataset.state===state));draw();}}
    document.querySelector('#live-status').textContent=`状态未知：${{error.message}}（不代表 Codex 报错）。可用按钮手动预览。`;
  }}
}},1000);
</script></html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("outputs/t31g-face"))
    parser.add_argument("--week-remaining", type=int, default=8,
                        help="Preview-only weekly quota remaining percent (0..100)")
    args = parser.parse_args()
    if not 0 <= args.week_remaining <= 100:
        parser.error("--week-remaining must be 0..100")
    args.out.mkdir(parents=True, exist_ok=True)
    for state in STATES:
        for phase in range(FRAMES):
            (args.out / f"{state}-{phase:02d}.png").write_bytes(
                png_bytes(render(state, phase, args.week_remaining)))
            (args.out / f"{state}-{phase:02d}-light.png").write_bytes(
                png_bytes(render(state, phase, args.week_remaining, inverted=True)))
    (args.out / "index.html").write_text(preview_html(args.week_remaining), encoding="utf-8")
    print(f"Wrote {len(STATES) * FRAMES * 2} native-size frames and {args.out / 'index.html'}")


if __name__ == "__main__":
    main()
