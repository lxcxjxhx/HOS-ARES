"""生成 HOS-ARES Launcher Icon PNG（紫色安全盾主题）"""
from PIL import Image, ImageDraw
import math
import os

DENSITIES = {
    "mdpi": 1,
    "hdpi": 1.5,
    "xhdpi": 2,
    "xxhdpi": 3,
    "xxxhdpi": 4,
}

BASE_DP = 108

def draw_shield_icon(size):
    """绘制紫色安全盾图标（透明背景，用于 adaptive foreground）"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    s = size / 108.0
    cx, cy = 54 * s, 54 * s

    def shield_path(top_off, rt_x, rb_x, bottom_y):
        return [
            (cx, cy - top_off),
            (rt_x, cy - top_off + 10*s),
            (rb_x, cy + (bottom_y - 54)*s),
            (cx, cy + (bottom_y - 54 + 12)*s),
            (2*cx - rb_x, cy + (bottom_y - 54)*s),
            (2*cx - rt_x, cy - top_off + 10*s),
        ]

    # Layer 1: Dark purple outer
    draw.polygon(shield_path(22*s, 78*s, 78*s, 90*s), fill="#5B21B6")
    # Layer 2: Medium purple
    draw.polygon(shield_path(24*s, 76*s, 76*s, 88*s), fill="#7C3AED")
    # Layer 3: Bright purple
    draw.polygon(shield_path(27*s, 73*s, 73*s, 85*s), fill="#A855F7")
    # Layer 4: Electric purple glow
    draw.polygon(shield_path(32*s, 68*s, 68*s, 80*s), fill="#C084FC")

    # AI Neural node hexagon
    hex_r = 10 * s
    hex_pts = []
    for i in range(6):
        angle = math.pi / 2 + i * math.pi / 3
        hex_pts.append((cx + hex_r * math.cos(angle), cy + hex_r * math.sin(angle)))
    draw.polygon(hex_pts, fill="#0B0710")
    draw.polygon(hex_pts, outline="#D946EF", width=max(1, int(2*s)))

    # Core pulse
    cr = 4 * s
    draw.ellipse([cx-cr, cy-cr, cx+cr, cy+cr], fill="#D946EF")
    # Core bright dot
    dr = 2.5 * s
    draw.ellipse([cx-dr, cy-dr, cx+dr, cy+dr], fill="#F3E8FF")

    # Circuit lines
    lw = max(1, int(1.5 * s))
    cc = "#D946EF"
    draw.line([(44*s, 45*s), (38*s, 40*s)], fill=cc, width=lw)
    draw.line([(46*s, 55*s), (40*s, 62*s)], fill=cc, width=lw)
    draw.line([(62*s, 45*s), (68*s, 40*s)], fill=cc, width=lw)
    draw.line([(60*s, 55*s), (66*s, 62*s)], fill=cc, width=lw)

    # Circuit dots
    dr2 = max(1, int(1.5 * s))
    for dx, dy in [(37,39), (39,63), (68,39), (67,63)]:
        draw.ellipse([dx*s-dr2, dy*s-dr2, dx*s+dr2, dy*s+dr2], fill="#D946EF")

    # Top highlight
    draw.polygon([(48*s,28*s),(54*s,25*s),(60*s,28*s),(54*s,35*s)], fill="#F3E8FF")

    return img


def draw_legacy_icon(size):
    """绘制传统方形图标（带圆角深紫背景）"""
    s = size / 108.0
    margin = 6 * s
    radius = 20 * s

    # Background
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([margin, margin, size-margin, size-margin],
                           radius=radius, fill="#0B0710")

    # Shield foreground
    shield = draw_shield_icon(size)

    # Mask for rounded corners
    mask = Image.new("L", (size, size), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle([margin, margin, size-margin, size-margin],
                                radius=radius, fill=255)

    return Image.composite(shield, img, mask)


def main():
    res_dir = r"c:\1AAA-PROJECT\HOS\HOS-ARES\app\app\src\main\res"

    for density, mult in DENSITIES.items():
        pixel_size = int(BASE_DP * mult)
        mipmap_dir = os.path.join(res_dir, f"mipmap-{density}")
        os.makedirs(mipmap_dir, exist_ok=True)

        legacy = draw_legacy_icon(pixel_size)
        legacy.save(os.path.join(mipmap_dir, "ic_launcher.png"), "PNG")

        fg = draw_shield_icon(pixel_size)
        fg.save(os.path.join(mipmap_dir, "ic_launcher_foreground.png"), "PNG")

        print(f"  {density}: {pixel_size}px ✓")

    print("\n✅ All launcher icons generated!")


if __name__ == "__main__":
    main()