"""Export an Xcursor theme as GTK 4.22's built-in Wayland cursor resources.

Without wp_cursor_shape_v1 (GNOME 46 lacks it) GTK 4.22 ignores the cursor
theme and draws the 32 px PNGs under /org/gtk/libgdk/cursor/, shown at
size^-1 * 32^2 logical px (43 px for size 24) with hard-coded hotspots.
G_RESOURCE_OVERLAYS can replace those PNGs: a canvas exactly `size` px wide
is shown 1:1 at `size` logical px, and each image is shifted so its own
hotspot lands on GTK's.

Usage: gtk-cursors.py THEME_CURSORS_DIR OUT_DIR [--size 24]
"""
import argparse
import os
import struct
import zlib

# GTK 4.22 gdk/wayland/gdkcursor-wayland.c textures[]: name -> hotspot.
HOTSPOTS = {
    "default": (5, 5), "context-menu": (5, 5), "help": (16, 27),
    "pointer": (14, 9), "progress": (5, 4), "wait": (11, 11), "cell": (15, 15),
    "crosshair": (15, 15), "text": (14, 15), "vertical-text": (16, 15),
    "alias": (12, 11), "copy": (12, 11), "move": (12, 11), "no-drop": (12, 11),
    "not-allowed": (12, 11), "grab": (10, 6), "grabbing": (15, 14),
    "e-resize": (25, 17), "n-resize": (17, 7), "ne-resize": (20, 13),
    "nw-resize": (13, 13), "s-resize": (17, 23), "se-resize": (19, 19),
    "sw-resize": (13, 19), "w-resize": (8, 17), "ew-resize": (16, 15),
    "ns-resize": (15, 17), "nesw-resize": (14, 14), "nwse-resize": (14, 14),
    "col-resize": (16, 15), "row-resize": (15, 17), "all-scroll": (15, 15),
    "zoom-in": (14, 13), "zoom-out": (14, 13), "dnd-ask": (5, 5),
    "all-resize": (12, 11),
}
FALLBACKS = {
    "ew-resize": ["size_hor", "sb_h_double_arrow"],
    "ns-resize": ["size_ver", "sb_v_double_arrow"],
    "all-resize": ["size_all", "fleur"],
    "dnd-ask": ["dnd-copy", "context-menu"],
}


def read_xcursor(path, size):
    """First frame at the nominal size closest to `size`: (w, h, hx, hy, argb)."""
    data = open(path, "rb").read()
    count = struct.unpack_from("<I", data, 12)[0]
    images = []
    for i in range(count):
        kind, nominal, pos = struct.unpack_from("<III", data, 16 + 12 * i)
        if kind == 0xFFFD0002:
            images.append((abs(nominal - size), pos))
    pos = min(images)[1]
    w, h, hx, hy = struct.unpack_from("<4I", data, pos + 16)
    return w, h, hx, hy, struct.unpack_from(f"<{w * h}I", data, pos + 36)


def write_png(path, size, rows):
    def chunk(tag, body):
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body))

    raw = b"".join(b"\0" + bytes(row) for row in rows)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)))
        f.write(chunk(b"IDAT", zlib.compress(raw, 9)))
        f.write(chunk(b"IEND", b""))


parser = argparse.ArgumentParser()
parser.add_argument("theme")
parser.add_argument("output")
parser.add_argument("--size", type=int, default=24)
args = parser.parse_args()
os.makedirs(args.output, exist_ok=True)

for name, (tx, ty) in HOTSPOTS.items():
    source = next(
        os.path.join(args.theme, n) for n in [name, *FALLBACKS.get(name, [])]
        if os.path.exists(os.path.join(args.theme, n))
    )
    w, h, hx, hy, argb = read_xcursor(source, args.size)
    # Shift toward GTK's hotspot, but never crop the image (help sits at
    # y = 27 of 32, e-resize at x = 25).
    xs = [i % w for i, p in enumerate(argb) if p >> 24]
    ys = [i // w for i, p in enumerate(argb) if p >> 24]
    dx = max(-min(xs), min(args.size - 1 - max(xs), tx - hx))
    dy = max(-min(ys), min(args.size - 1 - max(ys), ty - hy))
    rows = [bytearray(4 * args.size) for _ in range(args.size)]
    for y in range(h):
        for x in range(w):
            cx, cy = x + dx, y + dy
            if not (0 <= cx < args.size and 0 <= cy < args.size):
                continue
            p = argb[y * w + x]
            a = p >> 24
            if a:  # Xcursor pixels are premultiplied
                r, g, b = ((p >> s & 0xFF) * 255 // a for s in (16, 8, 0))
                rows[cy][4 * cx : 4 * cx + 4] = bytes((r, g, b, a))
    write_png(os.path.join(args.output, name), args.size, rows)
