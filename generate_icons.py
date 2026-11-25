"""Generate the two local PWA icons; run only for source/build preparation."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parent / "static"


def render(size: int) -> None:
    image = Image.new("RGB", (size, size), "#0b2026")
    draw = ImageDraw.Draw(image)
    margin = round(size * 0.12)
    draw.rounded_rectangle((margin, margin, size - margin, size - margin),
                           radius=round(size * 0.15), fill="#22ddd0")
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/seguisb.ttf", round(size * 0.36))
    except OSError:
        font = ImageFont.load_default(size=round(size * 0.36))
    bounds = draw.textbbox((0, 0), "FD", font=font)
    width, height = bounds[2] - bounds[0], bounds[3] - bounds[1]
    draw.text(((size - width) / 2 - bounds[0], (size - height) / 2 - bounds[1]),
              "FD", font=font, fill="#0b2026")
    image.save(ROOT / f"icon-{size}.png", format="PNG", optimize=True)


if __name__ == "__main__":
    for icon_size in (192, 512):
        render(icon_size)
