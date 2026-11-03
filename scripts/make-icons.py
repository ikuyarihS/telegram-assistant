"""Make every size of the Telegram Assistant icon from one picture.

    python scripts/make-icons.py          read assets/icon-source.png (square PNG, 512 px or more)
    python scripts/make-icons.py --draw   draw the built-in icon into assets/icon-source.png first

It writes, into src/tgkit/web/static (they ship with the package):
    tgkit.ico      16, 24, 32, 48, 64, 128 and 256 px: the Start menu, desktop and taskbar icon
    icon-256.png   the window icon on macOS and Linux
    favicon.png    64 px, the web page icon

To change the icon: replace assets/icon-source.png, run this script, commit the files.
Pure Python (zlib only): nothing to install. Reads 8-bit RGB or RGBA PNG files, gray too.
"""

import argparse
import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "assets" / "icon-source.png"
OUT = ROOT / "src" / "tgkit" / "web" / "static"
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


# ------------------------------------------------------------------ PNG read and write


def png_bytes(size: int, rgba: bytes) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    stride = size * 4
    raw = b"".join(b"\x00" + rgba[y * stride:(y + 1) * stride] for y in range(size))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def read_png(path: Path) -> tuple[int, int, bytearray]:
    """(width, height, RGBA bytes) of an 8-bit, non-interlaced PNG."""
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise SystemExit(f"{path} is not a PNG file.")
    pos, idat, head = 8, b"", None
    while pos < len(data):
        n = struct.unpack(">I", data[pos:pos + 4])[0]
        kind, body = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + n]
        if kind == b"IHDR":
            head = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat += body
        pos += 12 + n
    w, h, depth, ctype, _, _, interlace = head
    channels = {0: 1, 2: 3, 4: 2, 6: 4}.get(ctype)
    if depth != 8 or channels is None or interlace:
        raise SystemExit(f"{path}: save it as an 8-bit RGB or RGBA PNG (not interlaced, no palette).")
    raw, stride = zlib.decompress(idat), w * channels
    out, prev, i = bytearray(), bytearray(stride), 0
    for _ in range(h):
        ftype, line = raw[i], bytearray(raw[i + 1:i + 1 + stride])
        i += 1 + stride
        for x in range(stride):
            a = line[x - channels] if x >= channels else 0
            b = prev[x]
            c = prev[x - channels] if x >= channels else 0
            if ftype == 1:
                line[x] = (line[x] + a) & 255
            elif ftype == 2:
                line[x] = (line[x] + b) & 255
            elif ftype == 3:
                line[x] = (line[x] + (a + b) // 2) & 255
            elif ftype == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        prev = line
        for x in range(w):
            px = line[x * channels:(x + 1) * channels]
            if channels == 1:
                out += bytes((px[0], px[0], px[0], 255))
            elif channels == 2:
                out += bytes((px[0], px[0], px[0], px[1]))
            elif channels == 3:
                out += bytes((px[0], px[1], px[2], 255))
            else:
                out += px
    return w, h, out


def resize(w: int, h: int, rgba: bytearray, size: int) -> bytes:
    """Area average (premultiplied alpha) of a square crop to size x size."""
    side = min(w, h)
    ox, oy = (w - side) // 2, (h - side) // 2
    out = bytearray()
    for ty in range(size):
        y0, y1 = oy + ty * side // size, oy + max(ty * side // size + 1, (ty + 1) * side // size)
        for tx in range(size):
            x0, x1 = ox + tx * side // size, ox + max(tx * side // size + 1, (tx + 1) * side // size)
            r = g = b = a = 0
            for y in range(y0, y1):
                row = y * w * 4
                for x in range(x0, x1):
                    p = row + x * 4
                    al = rgba[p + 3]
                    r += rgba[p] * al
                    g += rgba[p + 1] * al
                    b += rgba[p + 2] * al
                    a += al
            n = (y1 - y0) * (x1 - x0)
            out += bytes((r // a, g // a, b // a, a // n) if a else (0, 0, 0, 0))
    return bytes(out)


def ico_bytes(images: list[tuple[int, bytes]]) -> bytes:
    """A Windows .ico holding PNG images (Windows Vista and later read them at every size)."""
    head = struct.pack("<HHH", 0, 1, len(images))
    offset, table, blobs = 6 + 16 * len(images), b"", b""
    for size, png in images:
        dim = 0 if size >= 256 else size
        table += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset)
        blobs += png
        offset += len(png)
    return head + table + blobs


# ------------------------------------------------------------------ the built-in icon


def _round_rect(x, y, x0, y0, x1, y1, r) -> bool:
    cx, cy = min(max(x, x0 + r), x1 - r), min(max(y, y0 + r), y1 - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r and x0 <= x <= x1 and y0 <= y <= y1


def _near_segment(x, y, ax, ay, bx, by, half) -> bool:
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy)))
    return (x - ax - t * dx) ** 2 + (y - ay - t * dy) ** 2 <= half * half


def _in_tail(x, y) -> bool:
    (ax, ay), (bx, by), (cx, cy) = (0.30, 0.62), (0.27, 0.84), (0.50, 0.66)

    def side(px, py, qx, qy):
        return (x - qx) * (py - qy) - (px - qx) * (y - qy)
    s1, s2, s3 = side(ax, ay, bx, by), side(bx, by, cx, cy), side(cx, cy, ax, ay)
    return (s1 <= 0 and s2 <= 0 and s3 <= 0) or (s1 >= 0 and s2 >= 0 and s3 >= 0)


def draw(size: int = 1024) -> bytes:
    """A blue tile with a white speech bubble and a blue checkmark: readable at 16 px,
    on light and dark taskbars alike (the tile is opaque). The same picture as assets/icon.svg."""
    top, bottom, ink = (30, 120, 214), (9, 76, 150), (11, 92, 173)
    out = bytearray()
    for py in range(size):
        y = (py + 0.5) / size
        tile = tuple(round(t + (b - t) * y) for t, b in zip(top, bottom))
        for px in range(size):
            x = (px + 0.5) / size
            if not _round_rect(x, y, 0.0, 0.0, 1.0, 1.0, 0.22):
                out += b"\x00\x00\x00\x00"
                continue
            colour = tile
            if _round_rect(x, y, 0.17, 0.19, 0.83, 0.67, 0.13) or _in_tail(x, y):
                colour = (255, 255, 255)
                if (_near_segment(x, y, 0.335, 0.435, 0.455, 0.555, 0.045)
                        or _near_segment(x, y, 0.455, 0.555, 0.665, 0.33, 0.045)):
                    colour = ink
            out += bytes((*colour, 255))
    return bytes(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--draw", action="store_true", help="draw the built-in icon into assets/icon-source.png")
    ap.add_argument("--source", type=Path, default=SOURCE)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args(argv)
    if args.draw or not args.source.exists():
        args.source.parent.mkdir(parents=True, exist_ok=True)
        args.source.write_bytes(png_bytes(1024, draw(1024)))
        print(f"Drew the built-in icon into {args.source}")
    w, h, rgba = read_png(args.source)
    if min(w, h) < 256:
        print(f"{args.source} is {w}x{h}; use 512 px or more for a sharp icon.", file=sys.stderr)
    images = [(s, png_bytes(s, resize(w, h, rgba, s))) for s in ICO_SIZES]
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "tgkit.ico").write_bytes(ico_bytes(images))
    (args.out / "icon-256.png").write_bytes(dict(images)[256])
    (args.out / "favicon.png").write_bytes(dict(images)[64])
    print(f"Wrote tgkit.ico ({', '.join(str(s) for s in ICO_SIZES)} px), icon-256.png and favicon.png "
          f"into {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
