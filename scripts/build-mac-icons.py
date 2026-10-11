"""Generate reproducible app and macOS template tray icons."""

from pathlib import Path

from PIL import Image, ImageDraw

PROJECT = Path(__file__).resolve().parents[1]
ASSETS = PROJECT / "build/mac-assets"


def glyph(draw, scale, color):
    def points(values):
        return [(x * scale, y * scale) for x, y in values]

    draw.line(
        points([(5, 13), (5, 6), (7, 4), (11, 4), (13, 6), (13, 13)]),
        fill=color,
        width=max(1, round(2 * scale)),
        joint="curve",
    )
    draw.rounded_rectangle(
        (7 * scale, 1 * scale, 11 * scale, 5 * scale),
        radius=scale,
        outline=color,
        width=max(1, round(scale)),
    )
    draw.line(
        points([(2, 10), (4, 10), (6, 7), (8, 12), (10, 8), (12, 10), (16, 10)]),
        fill=color,
        width=max(1, round(scale)),
        joint="curve",
    )


def main():
    ASSETS.mkdir(parents=True, exist_ok=True)
    for size, name, dpi in [(18, "trayTemplate.png", 72), (36, "trayTemplate@2x.png", 144)]:
        large = Image.new("RGBA", (size * 4, size * 4))
        glyph(ImageDraw.Draw(large), size * 4 / 18, "black")
        large.resize((size, size), Image.Resampling.LANCZOS).save(ASSETS / name, dpi=(dpi, dpi))
    icon = Image.new("RGBA", (1024, 1024))
    draw = ImageDraw.Draw(icon)
    draw.rounded_rectangle((40, 40, 984, 984), radius=212, fill="#163b5c")
    draw.rounded_rectangle((125, 125, 899, 899), radius=154, fill="#1766c2")
    symbol = Image.new("RGBA", (720, 720))
    glyph(ImageDraw.Draw(symbol), 40, "white")
    icon.alpha_composite(symbol, (152, 192))
    directory = ASSETS / "app.iconset"
    directory.mkdir(exist_ok=True)
    for size in [16, 32, 128, 256, 512]:
        for factor in [1, 2]:
            suffix = "@2x" if factor == 2 else ""
            icon.resize((size * factor, size * factor), Image.Resampling.LANCZOS).save(
                directory / f"icon_{size}x{size}{suffix}.png"
            )


if __name__ == "__main__":
    main()
