"""把叠构剖面渲染成图片流/文件：与界面同一数据模型，报告不再只有文字层表。"""
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

from src.domain.stackup_visual import COPPER, DIELECTRIC, FINISH, MASK, build_visual

COPPER_COLORS = {"brd": (92, 138, 166), "confirmed": (217, 130, 43), "missing": (148, 163, 184)}
OTHER_COLORS = {MASK: (46, 125, 79), FINISH: (122, 95, 168), DIELECTRIC: (238, 242, 247)}
DRILL_COLORS = {"PTH": "#C00000", "NPTH": "#7C3AED"}


def _font(size):
    for name in ("msyh.ttc", "segoeui.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def render_stackup(rows, *, board_thickness_mm=None, surface_finish="", holes=None, width=1180, scale=1.0):
    """返回 BytesIO PNG；纯数据绘制，不读取界面控件。"""
    visual = build_visual(rows, board_thickness_mm=board_thickness_mm, surface_finish=surface_finish, holes=holes)
    bands = visual["bands"]
    known = [band["thickness_um"] for band in bands if band["kind"] == COPPER and band["thickness_um"] is not None]
    tallest = max(known or [18])
    heights = []
    for band in bands:
        if band["kind"] == COPPER and band["thickness_um"] is not None:
            heights.append(max(11.0, band["thickness_um"] / tallest * 40 * scale))
        elif band["kind"] == COPPER:
            heights.append(22 * scale)
        else:
            heights.append(14 * scale)
    header, footer = 74 * scale, 96 * scale
    image = Image.new("RGB", (width, int(sum(heights) + header + footer)), "#FFFFFF")
    draw = ImageDraw.Draw(image)
    title, small = _font(int(21 * scale)), _font(int(15 * scale))
    draw.text((24 * scale, 16 * scale), "叠构剖面（设计/确认铜厚，非实物切片）", font=title, fill="#1F2937")
    draw.text((24 * scale, 40 * scale), visual["scale_note"], font=small, fill="#6B7280")
    thickness_note = (f"板厚 {visual['board_thickness_mm']:g} mm" if visual["board_thickness_mm"] is not None
                      else "板厚未获取")
    draw.text((width - 24 * scale, 40 * scale), thickness_note, font=small,
              fill="#B45309" if visual["board_thickness_mm"] is None else "#6B7280", anchor="ra")
    left, right = int(190 * scale), int(width - 470 * scale)
    drill_x = width - int(70 * scale)
    y = top = header
    for band, height in zip(bands, heights):
        if band["kind"] == COPPER:
            color = COPPER_COLORS[band["status"]]
            label = band["label"]
            value = (f"{band['thickness_um']:g} μm" if band["thickness_um"] is not None else "铜厚未获取")
            note = ("外层" if band["outer"] else "内层") + " · " + str(band["source"])[:30]
        else:
            color = OTHER_COLORS[band["kind"]]
            label = band["label"]
            value = "厚度未提供"
            note = ("介质厚度未给，等分仅示意" if band["kind"] == DIELECTRIC
                    else "按已确认表面处理显示" if band["kind"] == FINISH else "阻焊厚度需叠层资料")
        draw.rectangle((left, y, right, y + height), fill=color, outline="#D7DEE8")
        if band["kind"] == COPPER and band["status"] == "missing":
            for offset in range(int(left), int(right), int(12 * scale)):
                draw.line((offset, y + height, min(offset + height, right), y), fill="#FFFFFF")
        draw.text((left - 12 * scale, y + height / 2 - 9 * scale), label, font=small, fill="#1F2937", anchor="ra")
        draw.text((right + 14 * scale, y + height / 2 - 9 * scale), value, font=small, fill="#374151")
        draw.text((right + 132 * scale, y + height / 2 - 8 * scale), note, font=_font(int(12 * scale)), fill="#94A3B8")
        y += height + 4 * scale
    bottom = y - 4 * scale
    positions = {}
    cursor = top
    for band, height in zip(bands, heights):
        if band["kind"] == COPPER:
            positions[band["index"]] = (cursor, cursor + height)
        cursor += height + 4 * scale
    for index, drill in enumerate(visual["drills"]):
        offset = drill_x - index * int(26 * scale)
        through = [(positions[i][0], positions[i][1]) for i in range(drill["top"], drill["bottom"] + 1) if i in positions]
        if not through:
            continue
        color = DRILL_COLORS.get(drill["kind"], "#C00000")
        start, stop = through[0][0], through[-1][1]
        if drill["kind"] == "PTH":
            draw.line((offset, start, offset, stop), fill=color, width=max(2, int(3 * scale)))
        else:
            step = max(7.0, 11 * scale)
            cursor = start
            while cursor < stop:
                draw.line((offset, cursor, offset, min(cursor + step * .5, stop)), fill=color,
                          width=max(2, int(3 * scale)))
                cursor += step
        for edge in (start, stop):
            draw.line((offset - 8 * scale, edge, offset + 8 * scale, edge), fill=color, width=max(1, int(2 * scale)))
        draw.text((offset, bottom + (16 + index * 22) * scale), f"{drill['kind']} x{drill['spans']}",
                  font=small, fill=color, anchor="ma")
    x = 24 * scale
    y = bottom
    for label, color in (("BRD设计铜厚", COPPER_COLORS["brd"]), ("成品确认铜厚", COPPER_COLORS["confirmed"]),
                         ("未获取", COPPER_COLORS["missing"]), ("阻焊", OTHER_COLORS[MASK]),
                         ("表面处理", OTHER_COLORS[FINISH])):
        draw.rectangle((x, y + 26 * scale, x + 16 * scale, y + 40 * scale), fill=color, outline="#D7DEE8")
        draw.text((x + 22 * scale, y + 27 * scale), label, font=small, fill="#4B5563")
        x += int(150 * scale)
    for index, note in enumerate(visual["notes"][:2]):
        draw.text((24 * scale, y + 48 * scale + index * 19 * scale), "· " + note, font=small, fill="#B45309")
    if visual["drills"]:
        draw.text((right + 132 * scale, bottom + 32 * scale),
                  "孔符号只表示快照给出的跨度，不表示逐孔位置；实线 PTH、虚线 NPTH",
                  font=_font(int(12 * scale)), fill="#94A3B8")
    else:
        draw.text((right + 132 * scale, bottom + 32 * scale), "未取得孔跨度资料，剖面不绘制孔",
                  font=_font(int(12 * scale)), fill="#94A3B8")
    stream = BytesIO()
    image.save(stream, format="PNG")
    stream.seek(0)
    return stream


def render_stackup_png(rows, *, path, **options):
    stream = render_stackup(rows, **options)
    target = path
    with open(target, "wb") as handle:
        handle.write(stream.getvalue())
    return str(target)
