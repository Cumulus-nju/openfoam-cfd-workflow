"""Generate UrbanWind app icon — wind swirl on dark gradient background."""
from PIL import Image, ImageDraw, ImageFont
import math

SIZE = 256
ico = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
draw = ImageDraw.Draw(ico)

# ── Dark rounded square background ──
R = 48  # corner radius
margin = 4
# Draw rounded rect manually
for y in range(SIZE):
    for x in range(SIZE):
        # Check if inside rounded rect
        inside = True
        if x < margin: inside = False
        elif x > SIZE - margin: inside = False
        elif y < margin: inside = False
        elif y > SIZE - margin: inside = False
        else:
            # Corner checks
            corners = [
                (margin + R, margin + R),           # top-left
                (SIZE - margin - R, margin + R),    # top-right
                (margin + R, SIZE - margin - R),    # bottom-left
                (SIZE - margin - R, SIZE - margin - R),  # bottom-right
            ]
            for cx, cy in corners:
                dx, dy = x - cx, y - cy
                if (dx * dx + dy * dy) > R * R:
                    if (x < cx and y < cy) or (x >= cx and y < cy) or (x < cx and y >= cy) or (x >= cx and y >= cy):
                        dist = math.sqrt(dx * dx + dy * dy)
                        if dist > R:
                            inside = False
                            break

        if inside:
            # Gradient from dark navy (top) to slightly lighter (bottom)
            t = y / SIZE
            r = int(20 + 10 * t)
            g = int(25 + 15 * t)
            b = int(55 + 25 * t)
            ico.putpixel((x, y), (r, g, b, 255))

# ── Wind swirl streamlines (cyan accent) ──
cx, cy = SIZE // 2, SIZE // 2

def draw_streamline(draw, points, color, width=3):
    """Draw a smooth line through points."""
    for i in range(len(points) - 1):
        draw.line([points[i], points[i+1]], fill=color, width=width)

cyan_bright = (0, 220, 240, 255)
cyan_soft = (0, 180, 200, 180)
cyan_dim = (0, 140, 170, 130)

# Top streamline (incoming wind, strong)
pts1 = [(40, 100), (80, 95), (120, 88), (160, 85), (200, 88), (230, 95)]
draw_streamline(draw, pts1, cyan_bright, width=4)

# Middle streamline (main flow)
pts2 = [(30, 140), (70, 138), (120, 130), (170, 125), (210, 128), (240, 135)]
draw_streamline(draw, pts2, cyan_bright, width=5)

# Bottom streamline
pts3 = [(50, 180), (90, 178), (130, 172), (170, 170), (200, 175), (225, 182)]
draw_streamline(draw, pts3, cyan_soft, width=3)

# ── Small building silhouettes (bottom area, barely visible) ──
building_color = (0, 160, 200, 80)
# Building 1
draw.rectangle([(75, 170), (105, 200)], fill=building_color)
# Building 2
draw.rectangle([(130, 160), (155, 200)], fill=building_color)
# Building 3
draw.rectangle([(170, 175), (200, 200)], fill=building_color)

# ── Wind arrow indicator ──
arrow_tip = (200, 60)
arrow_color = (0, 255, 255, 220)
draw.polygon([arrow_tip, (185, 80), (195, 75), (215, 75), (205, 80)], fill=arrow_color)

# ── Subtle glow ring ──
for angle in range(0, 360, 2):
    rad = math.radians(angle)
    r = 108
    x = cx + (r + 2) * math.cos(rad)
    y = cy + (r - 10) * math.sin(rad)
    alpha = 80 + 40 * math.sin(rad * 3)
    draw.ellipse([x-1, y-1, x+1, y+1], fill=(0, 200, 255, int(alpha)))

# Save as ICO (multi-resolution)
ico.save(r"D:\Phase2_CFD_ML\urbanwind.ico", format="ICO", sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
print("Icon saved: D:\\Phase2_CFD_ML\\urbanwind.ico")
