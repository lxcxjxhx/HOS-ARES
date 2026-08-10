# -*- coding: utf-8 -*-
"""HOS-ARES 图标生成：安全风信子 x 百眼巨人（Argus Panoptes）——深紫黑渐变 + 霓虹紫眼睛。
纯 stdlib 输出 PNG。用法: python make_icons.py
"""
import zlib, struct, math, os

# PLAN3 配色
BG0 = (11, 7, 16)      # #0B0710 深紫灰
BG1 = (0, 0, 0)        # #000000 墨黑
VIOLET = (168, 85, 247)   # #A855F7 电光紫罗兰
MAGENTA = (217, 70, 239)  # #D946EF 洋红紫

def clamp(v, a, b): return max(a, min(b, v))

def write_png(path, w, h, pixels):
    """pixels: list of (r,g,b,a) rows -> PNG RGBA"""
    raw = b''
    for row in pixels:
        raw += b'\x00' + b''.join(struct.pack('4B', *px) for px in row)
    def chunk(typ, data):
        c = struct.pack('>I', len(data)) + typ + data
        c += struct.pack('>I', zlib.crc32(typ + data) & 0xffffffff)
        return c
    ihdr = struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0)
    png = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', ihdr) + chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b'')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(png)

def eye_scene(u, v, scale):
    """返回该像素的颜色（含 alpha）。u,v ∈ [0,1]，中心 (0.5,0.5)。scale 为内容缩放。"""
    cx, cy = 0.5, 0.5
    x = (u - cx) / scale + cx
    y = (v - cy) / scale + cy
    # 背景：深紫黑径向渐变
    r = math.hypot(x - cx, y - cy) * 1.7
    bg = tuple(int(BG0[i] + (BG1[i] - BG0[i]) * clamp(r, 0, 1)) for i in range(3))

    # 眼睛：外椭圆环 + 瞳孔圆 + 高光
    ex, ey = (x - cx) / 0.36, (y - cy) / 0.26   # 椭圆归一
    ed = math.hypot(ex, ey)
    # 眼环：发光描边（soft edge）
    ring = clamp((0.94 - ed) * 40, 0, 1) * clamp((ed - 0.55) * 25, 0, 1)
    # 虹膜（瞳孔外晕）
    iris = clamp((0.55 - ed) * 18, 0, 1)
    # 瞳孔
    pd = math.hypot((x - cx) / 0.13, (y - cy) / 0.13)
    pupil = clamp((1.0 - pd) * 30, 0, 1)
    # 高光
    hx, hy = cx + 0.05, cy - 0.055
    hd = math.hypot(x - hx, y - hy)
    hl = clamp((0.055 - hd) * 200, 0, 1)

    glow = ring * 0.55 + iris * 0.35
    c = [int(clamp(VIOLET[i] * glow + MAGENTA[i] * (pupil * 0.85) + 255 * hl, 0, 255)) for i in range(3)]
    a = int(clamp((glow + pupil * 0.9 + hl), 0, 1) * 255)
    return (c[0], c[1], c[2], a)

def render(size, scale, transparent_bg):
    px = []
    for j in range(size):
        row = []
        for i in range(size):
            u, v = (i + 0.5) / size, (j + 0.5) / size
            r, g, b, a = eye_scene(u, v, scale)
            if transparent_bg:
                row.append((r, g, b, a))
            else:
                # 不透明版本：背景铺满
                row.append((r, g, b, max(a, 255 if True else 0)))
        px.append(row)
    return px

def render_launcher(size, scale):
    """launcher：不透明背景 + 眼睛叠加。"""
    px = []
    for j in range(size):
        row = []
        for i in range(size):
            u, v = (i + 0.5) / size, (j + 0.5) / size
            cx, cy = 0.5, 0.5
            r = math.hypot(u - cx, v - cy) * 1.7
            bg = tuple(int(BG0[i] + (BG1[i] - BG0[i]) * clamp(r, 0, 1)) for i in range(3))
            er, eg, eb, ea = eye_scene(u, v, scale)
            row.append((
                int(bg[0] * (255 - ea) / 255 + er * ea / 255),
                int(bg[1] * (255 - ea) / 255 + eg * ea / 255),
                int(bg[2] * (255 - ea) / 255 + eb * ea / 255),
                255,
            ))
        px.append(row)
    return px

DENS = {'mdpi': (48, 108), 'hdpi': (72, 162), 'xhdpi': (96, 216),
        'xxhdpi': (144, 324), 'xxxhdpi': (192, 432)}
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'app/app/src/main/res')

for d, (lsz, fsz) in DENS.items():
    base = os.path.join(ROOT, f'mipmap-{d}')
    write_png(os.path.join(base, 'ic_launcher.png'), lsz, lsz, render_launcher(lsz, 0.78))
    # foreground：透明背景，内容缩到自适应图标安全区（~0.62 视口）
    write_png(os.path.join(base, 'ic_launcher_foreground.png'), fsz, fsz, render(fsz, 0.62, True))
    print('生成', d, f'{lsz}px / fg {fsz}px')

# adaptive 背景色：安全风信子深紫
bg_path = os.path.join(ROOT, 'values/ic_launcher_background.xml')
with open(bg_path, 'w', newline='\n') as f:
    f.write('<?xml version="1.0" encoding="utf-8"?>\n<resources>\n    <color name="ic_launcher_background">#0B0710</color>\n</resources>\n')
print('背景色 -> #0B0710')
