"""Bake a frosted-glass terminal background from a wallpaper.

Ghostty shows this image opaque, so no compositor blur is involved: the
wallpaper is cover-cropped, blurred like an iOS material (large Gaussian,
mild saturation), toned for the theme and mixed into the theme background.

Usage: frost-background.py IN OUT --bg '#1e2233' [--tint 0.62] [--blur 34]
         [--saturate 1.2] [--dim 1.0] [--lift 1.0] [--size 2560x1440]
"""
import argparse

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter

parser = argparse.ArgumentParser()
parser.add_argument("source")
parser.add_argument("output")
parser.add_argument("--bg", required=True)
parser.add_argument("--tint", type=float, default=0.62)
parser.add_argument("--blur", type=float, default=34.0)
parser.add_argument("--saturate", type=float, default=1.2)
parser.add_argument("--dim", type=float, default=1.0)
parser.add_argument("--lift", type=float, default=1.0)
parser.add_argument("--size", default="2560x1440")
args = parser.parse_args()

width, height = (int(v) for v in args.size.split("x"))
art = Image.open(args.source).convert("RGB")
scale = max(width / art.width, height / art.height)
art = art.resize((round(art.width * scale), round(art.height * scale)), Image.LANCZOS)
left, top = (art.width - width) // 2, (art.height - height) // 2
art = art.crop((left, top, left + width, top + height)).filter(ImageFilter.GaussianBlur(args.blur))
art = np.asarray(ImageEnhance.Color(art).enhance(args.saturate), float) * args.dim
art = 255.0 - (255.0 - art) * args.lift

bg = np.array([int(args.bg.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4)], float)
image = bg * args.tint + art * (1 - args.tint)
image += np.random.default_rng(0).uniform(-0.5, 0.5, image.shape)  # dither
Image.fromarray(image.round().clip(0, 255).astype(np.uint8)).save(args.output, optimize=True)
