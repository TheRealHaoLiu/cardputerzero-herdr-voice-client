"""Draw the launcher tile: a microphone on a dark rounded square. Usage: make_icon.py <out.png>"""
import sys

from PIL import Image, ImageDraw

S = 100
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle((4, 4, S - 4, S - 4), radius=22, fill=(24, 28, 36, 255))
amber = (255, 176, 46, 255)
d.rounded_rectangle((38, 18, 62, 58), radius=12, fill=amber)          # capsule
d.arc((28, 34, 72, 72), start=0, end=180, fill=amber, width=5)        # cradle
d.line((50, 72, 50, 82), fill=amber, width=5)                         # stem
d.line((38, 83, 62, 83), fill=amber, width=5)                         # foot
img.save(sys.argv[1])
