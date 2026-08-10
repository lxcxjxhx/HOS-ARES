# -*- coding: utf-8 -*-
"""生成仓库 logo（512）与 banner（1200x630）：深紫黑渐变 + 百眼巨人紫眼。"""
import zlib, struct, math, os

BG0 = (11, 7, 16); BG1 = (0, 0, 0)
VIOLET = (168, 85, 247); MAGENTA = (217, 70, 239)

def clamp(v, a, b): return max(a, min(b, v))

def write_png(path, w, h, pix):
    raw = b''
    for row in pix:
        raw += b'\x00' + b''.join(struct.pack('4B', *px) for px in row)
    def chunk(t, d):
        c = struct.pack('>I', len(d)) + t + d
        return c + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
    ihdr = struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0)
    png = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', ihdr) + chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b'')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f: f.write(png)

def eye_at(u, v, cx, cy, s):
    """眼睛：cx,cy 中心（0-1），s 缩放。返回 (r,g,b,a)。"""
    x = (u - cx) / s + 0.5
    y = (v - cy) / s + 0.5
    ex = (x - 0.5) / 0.36; ey = (y - 0.5) / 0.26
    ed = math.hypot(ex, ey)
    ring = clamp((0.94 - ed) * 40, 0, 1) * clamp((ed - 0.55) * 25, 0, 1)
    iris = clamp((0.55 - ed) * 18, 0, 1)
    pd = math.hypot((x - 0.5) / 0.13, (y - 0.5) / 0.13)
    pupil = clamp((1.0 - pd) * 30, 0, 1)
    hx, hy = 0.55, 0.445
    hl = clamp((0.055 - math.hypot(x - hx, y - hy)) * 200, 0, 1)
    glow = ring * 0.55 + iris * 0.35
    c = [int(clamp(VIOLET[i] * glow + MAGENTA[i] * (pupil * 0.85) + 255 * hl, 0, 255)) for i in range(3)]
    return (c[0], c[1], c[2], int(clamp(glow + pupil * 0.9 + hl, 0, 1) * 255))

def bg_at(u, v, w, h):
    """深紫黑径向渐变背景（u,v 像素坐标）。"""
    r = math.hypot((u / w) - 0.5, (v / h) - 0.42) * 1.8
    t = clamp(r, 0, 1)
    return tuple(int(BG0[i] + (BG1[i] - BG0[i]) * t) for i in range(3))

def render(w, h, eye_cx, eye_cy, eye_s):
    rows = []
    for j in range(h):
        row = []
        for i in range(w):
            u, v = (i + 0.5) / w, (j + 0.5) / h
            bg = bg_at(i, j, w, h)
            er, eg, eb, ea = eye_at(u, v, eye_cx, eye_cy, eye_s)
            row.append((
                int(bg[0] * (255 - ea) / 255 + er * ea / 255),
                int(bg[1] * (255 - ea) / 255 + eg * ea / 255),
                int(bg[2] * (255 - ea) / 255 + eb * ea / 255), 255))
        rows.append(row)
    return rows

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'docs')
write_png(os.path.join(ROOT, 'logo.png'), 512, 512, render(512, 512, 0.5, 0.46, 0.82))
write_png(os.path.join(ROOT, 'banner.png'), 1200, 630, render(1200, 630, 0.5, 0.45, 0.5))
print('logo.png + banner.png 已生成')
