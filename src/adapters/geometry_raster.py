"""把查看器图形模型光栅化成 PNG 字节；纯计算，可在后台线程跑，不碰 Tk。

一次渲染产出一整张位图，界面只替换这一张图，因此不会像逐条重绘那样出现残影；
裁剪、点数预算和简化都记录在返回值里，界面可以如实标注“显示已简化/截断”。
"""
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

from src.domain.geometry_view import visible_layers

BACKGROUND = "#101820"
ROLE_COLORS = {"copper": "#cf9446", "mask": "#2fa36b", "paste": "#8fa3c0", "silkscreen": "#b6a4ff",
               "outline": "#e6edf6", "drill": "#5ac8d4", "unknown": "#8a93a3"}
LAYER_ALPHA = 118
ISSUE_COLOR = "#ff3b30"


def _font(size):
    for name in ("msyh.ttc", "segoeui.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _role_family(role):
    return str(role or "unknown").split("_")[0].split("/")[-1].strip().lower()


def _rgb(color):
    return (int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16))


BACKGROUND_RGB = _rgb(BACKGROUND)


def _inside(bounds, area):
    return bounds[2] >= area[0] and bounds[0] <= area[2] and bounds[3] >= area[1] and bounds[1] <= area[3]


def render_png(model, area=None, size=(1100, 760), layers=None, show_issues=True, scale_bar=True):
    """返回 (png_bytes, stats)。area 为毫米视野；size 为像素 (w, h)。"""
    width, height = max(int(size[0]), 64), max(int(size[1]), 64)
    area = list(area) if area else (model or {}).get("bounds")
    image = Image.new("RGB", (width, height), BACKGROUND)
    if not area or width < 2 or height < 2:
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue(), dict(drawn_layers=0, drawn_features=0, truncated=False,
                                       note="无图形范围，未绘制")
    x0, y0, x1, y1 = area
    span_x, span_y = max(x1 - x0, 1e-6), max(y1 - y0, 1e-6)
    scale = min((width - 16) / span_x, (height - 40) / span_y)
    offset_x = (width - span_x * scale) / 2
    offset_y = (height - span_y * scale) / 2 + 16

    def point(px, py):
        return (offset_x + (px - x0) * scale, offset_y + (y1 - py) * scale)

    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    paint = ImageDraw.Draw(overlay)
    selected = layers if layers is not None else visible_layers(model)
    drawn_layers = drawn_features = 0
    truncated = False
    for layer in selected:
        rings = layer.get("polygons", [])
        lines = layer.get("lines", [])
        if not rings and not lines:
            continue
        family = _role_family(layer.get("role"))
        color = _rgb(ROLE_COLORS.get(family, ROLE_COLORS["unknown"]))
        alpha = 255 if family == "outline" else LAYER_ALPHA
        for polygon in rings:
            if not polygon:
                continue
            outer = polygon[0]
            if len(outer) < 2:
                continue
            bounds = layer.get("bounds") or [min(p[0] for p in outer), min(p[1] for p in outer),
                                             max(p[0] for p in outer), max(p[1] for p in outer)]
            if not _inside(bounds, area):
                continue
            coordinates = [point(px, py) for px, py in outer]
            if len(coordinates) > 2:
                paint.polygon(coordinates, fill=color + (alpha,))
                for hole in polygon[1:]:
                    if len(hole) > 2:
                        paint.polygon([point(px, py) for px, py in hole], fill=BACKGROUND_RGB + (255,))
                drawn_features += 1
        for line in lines:
            if len(line) < 2:
                continue
            paint.line([point(px, py) for px, py in line], fill=color + (255,), width=1)
            drawn_features += 1
        drawn_layers += 1
        if layer.get("truncated"):
            truncated = True
    image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(image)
    markers = 0
    if show_issues:
        radius = max(4.0, min(width, height) / 160)
        small = _font(max(9, int(radius * 1.6)))
        for issue in (model or {}).get("issues", []):
            if not (area[0] <= issue["x_mm"] <= area[2] and area[1] <= issue["y_mm"] <= area[3]):
                continue
            px, py = point(issue["x_mm"], issue["y_mm"])
            draw.ellipse((px - radius, py - radius, px + radius, py + radius), outline=ISSUE_COLOR, width=2)
            draw.text((px, py), str(issue["index"]), fill=ISSUE_COLOR, font=small, anchor="mm")
            markers += 1
    if scale_bar:
        millimeters = span_x * 0.1
        for candidate in (50, 20, 10, 5, 2, 1, .5, .2, .1):
            if candidate <= millimeters:
                millimeters = candidate
                break
        length = millimeters * scale
        draw.line((16, height - 16, 16 + length, height - 16), fill="#cfd8e3", width=2)
        draw.text((16 + length + 6, height - 16), f"{millimeters:g} mm", fill="#cfd8e3", font=_font(12), anchor="la")
        draw.text((width - 16, 10), f"1 px ≈ {1 / scale:.4f} mm", fill="#8d99ab", font=_font(12), anchor="ra")
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=False)
    return buffer.getvalue(), dict(drawn_layers=drawn_layers, drawn_features=drawn_features, issues=markers,
                                   truncated=truncated, mm_per_pixel=1 / scale,
                                   note="显示已简化/截断，不能用于量测" if truncated else "")


def render_to_file(model, path, **options):
    data, stats = render_png(model, **options)
    with open(path, "wb") as handle:
        handle.write(data)
    return str(path), stats
