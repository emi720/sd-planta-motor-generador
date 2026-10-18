"""Genera los íconos de la aplicación: icon.png (Linux), icon.ico (Windows) e
icon.icns (macOS). Una cuadrada de entrada y la respuesta de primer orden.

Uso: python packaging/make_icon.py
"""
import math
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).parent
S = 1024  # se dibuja grande y se reduce: bordes suaves sin antialias propio

img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle([40, 40, S - 40, S - 40], radius=200, fill=(21, 101, 192, 255))

x0, x1, y_lo, y_hi = 150, S - 150, 720, 330
w = 26
# Entrada: cuadrada (un período y medio).
xs = [x0, x0 + 180, x0 + 180, x0 + 420, x0 + 420, x0 + 660, x0 + 660, x1]
ys = [y_lo, y_lo, y_hi, y_hi, y_lo, y_lo, y_hi, y_hi]
d.line(list(zip(xs, ys)), fill=(255, 255, 255, 255), width=w, joint="curve")

# Salida: primer orden siguiendo a la cuadrada.
pts, v, tau = [], y_lo, 70.0
for x in range(x0, x1 + 1, 4):
    target = y_hi if (x0 + 180 <= x < x0 + 420) or x >= x0 + 660 else y_lo
    v += (target - v) * (1 - math.exp(-4 / tau))
    pts.append((x, v + 40))
d.line(pts, fill=(255, 112, 67, 255), width=w, joint="curve")

icon = img.resize((512, 512), Image.LANCZOS)
icon.save(OUT / "icon.png")
icon.save(OUT / "icon.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
img.save(OUT / "icon.icns")
print("icon.png, icon.ico, icon.icns en", OUT)
