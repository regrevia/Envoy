#!/usr/bin/env python3
"""Render the envoy 60s demo: terminal-style replay of a real captured CLI run.

All text shown comes from the captured stdout (/tmp/envoy-demo-read.json); only
layout, reveal timing and the Chinese annotations are authored here.
"""
import json
import os
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1280, 720
MARGIN = 36
BG = (13, 17, 23)
PANEL = (22, 27, 34)
FG = (201, 209, 217)
DIM = (110, 118, 129)
GREEN = (63, 185, 80)
BLUE = (88, 166, 255)
AMBER = (210, 153, 34)
PURPLE = (188, 140, 255)
RED = (248, 81, 73)

MONO = "/System/Library/Fonts/Menlo.ttc"
FONT = ImageFont.truetype(MONO, 19)
FONT_SM = ImageFont.truetype(MONO, 16)
FONT_TITLE = ImageFont.truetype(MONO, 46)
FONT_SUB = ImageFont.truetype(MONO, 22)
CJK = "/System/Library/Fonts/Hiragino Sans GB.ttc"
FONT_CJK = ImageFont.truetype(CJK, 19, index=0)
FONT_CJK_SM = ImageFont.truetype(CJK, 16, index=0)
FONT_CJK_TITLE = ImageFont.truetype(CJK, 46, index=0)
FONT_CJK_SUB = ImageFont.truetype(CJK, 22, index=0)

LH = 27
CW = FONT.getlength("M")
COLS = int((W - 2 * MARGIN - 24) / CW)

DATA = json.load(open(os.environ.get("DEMO_INPUT", "/tmp/envoy-demo-read.json")))
VIDEO = DATA["coverage"]["modalities"]["video"]["details"]
DURATION = VIDEO["duration_seconds"]
MATERIAL = DATA["material_id"]
BLOCK = next(b for b in DATA["blocks"] if b["kind"] == "transcript")
LOC = BLOCK["locator"]
TRANSCRIPT = BLOCK["content"]["text"].split("\n\n")
TITLE = DATA["blocks"][0]["content"]["text"]
UNRESOLVED = DATA["coverage"]["unresolved"]


def mmss(sec):
    sec = int(round(sec))
    return f"{sec // 60:02d}:{sec % 60:02d}"


def _wide(ch):
    return ord(ch) > 0x2E7F


def _cells(text):
    return sum(2 if _wide(ch) else 1 for ch in text)


def wrap(text, cols, indent=""):
    out = []
    cur = ""
    for ch in text:
        cur += ch
        if _cells(cur) >= cols and ch == " ":
            out.append(cur.rstrip())
            cur = indent
    if cur.strip():
        out.append(cur.rstrip())
    return out


def draw_mixed(d, x, y, text, color, mono, cjk):
    """Draw a line that mixes ASCII (mono) and CJK (cjk) glyphs."""
    run, run_mono = "", True
    for ch in text:
        m = not _wide(ch)
        if run and m != run_mono:
            f = mono if run_mono else cjk
            d.text((x, y), run, font=f, fill=color)
            x += f.getlength(run)
            run = ""
        run += ch
        run_mono = m
    if run:
        f = mono if run_mono else cjk
        d.text((x, y), run, font=f, fill=color)


# ---------- scene script ----------
# Each entry: (start_time, duration_hint, kind, payload)
terminal_lines = []  # (text, color, appear_at)

T_CMD = 3.9
CMD = "packages/envoy-cli/.venv/bin/envoy read --request-json -"
REQ = [
    '{"request_id": "demo-1",',
    ' "source": {"url": "https://www.youtube.com/watch?v=iG9CE55wbtY"},',
    ' "budget": {"max_download_bytes": 40000000, "max_work_seconds": 600},',
    ' "scope": {"start_seconds": 0, "end_seconds": 30}}',
]


def add(text, color=FG, at=0.0, indent=0):
    terminal_lines.append((" " * indent + text, color, at))


def build_script():
    t = T_CMD
    # command line typed char by char
    add("$ " + CMD, GREEN, t)
    t += 0.9
    for row in REQ:
        add(row, DIM if row.startswith(" ") else DIM, t)
        t += 0.35
    add("", FG, t)
    t += 0.2
    add("  » 读取中 …  目标 20:03 的视频，请求 00:00–00:30", AMBER, t)
    t += 2.6
    add("  » 完成：2.2 s（本机实测，含系统代理）", GREEN, t)
    t += 0.5
    add("", FG, t)

    # response head
    add('{', FG, t)
    t += 0.25
    add('  "protocol_version": "envoy.read/1",', DIM, t)
    add('  "request_id": "demo-1",', DIM, t)
    add(f'  "material_id": "{MATERIAL}",', BLUE, t + 0.25)
    add('  "result": "ready",', GREEN, t + 0.5)
    add('  "execution_state": "completed",', DIM, t + 0.5)
    add('  "blocks": [ … ],', DIM, t + 0.5)
    add('  "coverage": { … }', DIM, t + 0.5)
    add('}', FG, t + 0.5)
    t += 3.2

    # blocks excerpt
    add("… 节选 1：blocks —— 每条内容都带 locator（时间 / 位置）", DIM, t)
    add('"blocks": [', FG, t + 0.2)
    add('  { "id": "blk-title", "kind": "heading",', DIM, t + 0.3)
    for row in wrap('"content": { "text": "' + TITLE + '" } },', COLS - 4, indent="      "):
        add("    " + row, FG, t + 0.4)
    add('  { "id": "blk-video-transcript", "kind": "transcript",', BLUE, t + 0.7)
    add('    "origin": "subtitle",', DIM, t + 0.8)
    add(f'    "locator": {{ "type": "video_time", "start_seconds": {LOC["start_seconds"]},', FG, t + 1.0)
    add(f'                 "end_seconds": {LOC["end_seconds"]} }},', FG, t + 1.0)
    add('    "content": { "type": "text", "language": "en",', DIM, t + 1.2)
    for row in TRANSCRIPT:
        add(f'      "text": "{row}"', GREEN, t + 1.4)
    add('}', FG, t + 1.4)
    t += 4.8

    # coverage excerpt
    add("… 节选 2：coverage —— 读过什么、没读什么，都在这里", DIM, t)
    add('"coverage": {', FG, t + 0.2)
    add('  "modalities": {', FG, t + 0.2)
    add('    "text":  { "extraction": "complete", "returned": 1, "extracted": 2 },', GREEN, t + 0.4)
    add('    "video": { "extraction": "partial",  "unresolved": [ … ] } },', AMBER, t + 0.6)
    add('  "unresolved": [', AMBER, t + 0.9)
    for item in UNRESOLVED:
        for row in wrap(f'"{item}",', COLS - 6, indent="      "):
            add("    " + row, AMBER, t + 1.1)
    add("  ]", FG, t + 1.1)
    add("}", FG, t + 1.1)
    t += 4.4

    # facts
    add("", FG, t)
    add("今天这一段读到了什么：", BLUE, t)
    add(f'  » 已覆盖  {mmss(LOC["start_seconds"])}.{int(round((LOC["start_seconds"] % 1) * 1000)):03d}'
        f' – {mmss(LOC["end_seconds"])}.{int(round((LOC["end_seconds"] % 1) * 1000)):03d}'
        '  （人工字幕，2 条）', GREEN, t + 0.4)
    add(f'  » 待覆盖  {mmss(30)}.000 – {mmss(DURATION)}.000  （其余 {mmss(DURATION - 30)} 未请求）', AMBER, t + 0.8)
    add("  ○ 画面未抽帧  visual_inspection: not_performed", AMBER, t + 1.2)
    add(f'  → 续读：envoy expand --material {MATERIAL} --offset 0 --scope 00:30-20:03', BLUE, t + 1.6)
    add("", FG, t + 1.6)
    t += 3.0

    # closing
    add("一条链接 → 可直接引用的 [mm:ss] 片段 → 待覆盖一目了然", PURPLE, t)
    add("budget 与 coverage 决定读多少；未覆盖的部分永远显式列出。", DIM, t + 0.5)
    add("", FG, t + 0.5)
    add('  $ envoy_read  {"url": "https://www.youtube.com/watch?v=iG9CE55wbtY"}', GREEN, t + 0.9)
    return t + 2.6


END_T = build_script()
TOTAL = END_T

TITLE_END = 3.4
FADE = 0.45


def draw_telnet_bg(d):
    d.rectangle([0, 0, W, H], fill=BG)
    d.rectangle([0, 0, W, 46], fill=PANEL)
    for i, c in enumerate([RED, AMBER, GREEN]):
        d.ellipse([18 + i * 22, 17, 30 + i * 22, 29], fill=c)
    draw_mixed(d, 96, 13, "envoy  ·  read  ·  demo", DIM, FONT_SM, FONT_CJK_SM)


def frame(t):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    if t < TITLE_END:
        # title card
        d.rectangle([0, 0, W, H], fill=BG)
        lines = [
            ("Envoy", FONT_TITLE, FONT_CJK_TITLE, GREEN, 250),
            ("一条链接，一段可以直接引用的片段。", FONT_SUB, FONT_CJK_SUB, FG, 330),
            ("读 YouTube：返回 [mm:ss] 字幕块，并明确列出未覆盖范围。", FONT, FONT_CJK, DIM, 372),
        ]
        for i, (text, mono, cjk, color, y) in enumerate(lines):
            a = min(1.0, max(0.0, (t - 0.15 * i) / 0.5))
            if a <= 0:
                continue
            col = tuple(int(BG[j] + (color[j] - BG[j]) * a) for j in range(3))
            draw_mixed(d, 90, y, text, col, mono, cjk)
        return img

    draw_telnet_bg(d)
    y = 70
    max_y = H - MARGIN
    visible = []
    for text, color, at in terminal_lines:
        if t >= at:
            visible.append((text, color))
    # scroll viewport: show tail if overflowing
    max_lines = (max_y - y) // LH
    start = max(0, len(visible) - max_lines)
    for text, color in visible[start:]:
        draw_mixed(d, MARGIN, y, text, color, FONT, FONT_CJK)
        y += LH

    # cursor blink at bottom of last line
    if int(t * 2) % 2 == 0 and y < max_y:
        d.rectangle([MARGIN, y - 2, MARGIN + int(CW), y + 18], fill=FG)

    # progress bar for the whole clip
    d.rectangle([0, H - 5, W, H], fill=PANEL)
    d.rectangle([0, H - 5, int(W * min(1.0, t / TOTAL)), H], fill=GREEN)
    return img


def main():
    fps = int(os.environ.get("DEMO_FPS", "20"))
    n = int(TOTAL * fps)
    out = Path(os.environ.get("DEMO_FRAMES", "/tmp/demo-frames"))
    out.mkdir(exist_ok=True)
    for f in out.glob("*.png"):
        f.unlink()
    for i in range(n):
        frame(i / fps).save(out / f"{i:05d}.png")
    print(f"frames={n} seconds={TOTAL:.1f} dir={out}")


if __name__ == "__main__":
    main()
