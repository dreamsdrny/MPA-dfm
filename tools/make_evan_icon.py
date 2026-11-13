"""Draw every ICO size independently: small-size branding must not be downscaled text."""
import io
import struct
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def draw_icon(size):
    # Dedicated horizontal EVAN wordmark at every native resolution.
    scale = 4
    canvas = Image.new("RGBA", (size * scale, size * scale), "#164FC4")
    draw = ImageDraw.Draw(canvas)
    font_path = Path("C:/Windows/Fonts/arialbd.ttf")
    if not font_path.is_file():
        raise RuntimeError("Windows Arial Bold font not found")
    for text, top in (("EVAN", 0),):
        font = ImageFont.truetype(str(font_path), int(size * scale * 0.36))
        bounds = draw.textbbox((0, 0), text, font=font)
        x = (size * scale - (bounds[2] - bounds[0])) / 2 - bounds[0]
        while bounds[2] - bounds[0] > size * scale - scale:
            font = ImageFont.truetype(str(font_path), font.size - 1)
            bounds = draw.textbbox((0, 0), text, font=font)
        x = (size * scale - (bounds[2] - bounds[0])) / 2 - bounds[0]
        y = (size * scale - (bounds[3] - bounds[1])) / 2 - bounds[1]
        draw.text((x, y), text, font=font, fill="white")
    return canvas.resize((size, size), Image.Resampling.LANCZOS)


def main():
    images = [(size, draw_icon(size)) for size in SIZES]
    entries, payloads = [], []
    offset = 6 + 16 * len(images)
    for size, image in images:
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        payload = stream.getvalue()
        entries.append(struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(payload), offset))
        payloads.append(payload)
        offset += len(payload)
    # Keep the original artwork for reference; only first generation backs it up.
    original = ROOT / "evan_original.ico"
    if not original.exists() and (ROOT / "evan.ico").exists():
        original.write_bytes((ROOT / "evan.ico").read_bytes())
    (ROOT / "evan.ico").write_bytes(struct.pack("<HHH", 0, 1, len(images)) + b"".join(entries + payloads))
    preview = Image.new("RGB", (400, 120), "#eeeeee")
    for x, size in zip((12, 50, 95, 150, 220), (16, 24, 32, 48, 64)):
        preview.paste(draw_icon(size), (x, 28), draw_icon(size))
    preview.save(ROOT / "docs" / "evan_icon_sizes.png")
    draw_icon(256).save(ROOT / "evan_taskbar.png")
    print("Generated native ICO sizes:", SIZES)


if __name__ == "__main__":
    main()
