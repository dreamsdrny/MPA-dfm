"""Documented field meaning, not drill display glyph/name guessing."""
from src.adapters.extracta_objects import graphic, parse_objects
from src.adapters.extracta import OBJECT_VIEWS


def test_line_arc_and_figure_fields():
    line = dict(GRAPHIC_DATA_NAME="LINE", GRAPHIC_DATA_NUMBER="257",
        GRAPHIC_DATA_1="0", GRAPHIC_DATA_2="0", GRAPHIC_DATA_3="10", GRAPHIC_DATA_4="0", GRAPHIC_DATA_5="5")
    assert graphic(line, .0254)["width_mm"] == .127
    arc = dict(line, GRAPHIC_DATA_NAME="ARC", GRAPHIC_DATA_1="10", GRAPHIC_DATA_3="0", GRAPHIC_DATA_4="10",
        GRAPHIC_DATA_5="0", GRAPHIC_DATA_6="0", GRAPHIC_DATA_7="10", GRAPHIC_DATA_8="5", GRAPHIC_DATA_9="COUNTERCLOCKWISE")
    value = graphic(arc, .0254)
    assert value["points"][0] == (.254, 0)
    assert value["points"][-1] == (0, .254)
    assert value["width_mm"] == .127


def test_real_drill_name_not_display_figure(tmp_path):
    for name, (_, fields) in OBJECT_VIEWS.items():
        lines = ["A!"+"!".join(fields)+"!", "J!board!date!0!0!1!1!0.01!mils!"]
        if name == "pads":
            row = dict(CLASS="VIA CLASS", PAD_STACK_NAME="CUSTOM", VIA_X="0", VIA_Y="10", NET_NAME="GND",
                DRILL_HOLE_X="0", DRILL_HOLE_Y="10", DRILL_HOLE_NAME="8", DRILL_HOLE_NAME2="8",
                DRILL_FIGURE_WIDTH="100", DRILL_FIGURE_HEIGHT="100", DRILL_FIGURE_SHAPE="SQUARE", DRILL_HOLE_PLATING="PLATED")
            lines.append("S!"+"!".join(row.get(field, "") for field in fields)+"!")
        (tmp_path / (name+".out")).write_text("\n".join(lines)+"\n")
    data = parse_objects(tmp_path, "mils", [])
    assert data["holes"][0]["drill_mm"] == .2032
    assert data["holes"][0]["process"] is None
    assert data["vias"][0]["net"] == "GND"


def test_silkscreen_revision_text_retains_actual_text_and_side(tmp_path):
    for name, (_, fields) in OBJECT_VIEWS.items():
        lines=["A!"+"!".join(fields)+"!", "J!board!date!0!0!1!1!0.01!mils!"]
        if name=="geometry":
            row=dict(CLASS="BOARD GEOMETRY",SUBCLASS="SILKSCREEN_TOP",GRAPHIC_DATA_NAME="TEXT",GRAPHIC_DATA_7="BOARD-REV-A")
            lines.append("S!"+"!".join(row.get(field, "") for field in fields)+"!")
        (tmp_path/(name+".out")).write_text("\n".join(lines)+"\n")
    data=parse_objects(tmp_path,"mils",[])
    assert data["silkscreen_text_records"][0]["text"]=="BOARD-REV-A"
    assert data["silkscreen_text_records"][0]["side"]=="TOP"
    assert data["complete"]["silkscreen_text_records"] is True


def test_standard_rectangle_uses_absolute_pin_rotation_only_not_world_lines():
    from shapely.geometry import Polygon
    row=dict(CLASS="PIN",GRAPHIC_DATA_NAME="FIG_RECTANGLE",GRAPHIC_DATA_NUMBER="6",GRAPHIC_DATA_1="10",GRAPHIC_DATA_2="20",GRAPHIC_DATA_3="4",GRAPHIC_DATA_4="2",PIN_ROTATION_ABSOLUTE="270")
    assert Polygon(graphic(row,1)["polygon"]).bounds==(9,18,11,22)
    line=dict(row,GRAPHIC_DATA_NAME="LINE",GRAPHIC_DATA_1="0",GRAPHIC_DATA_2="0",GRAPHIC_DATA_3="10",GRAPHIC_DATA_4="0",GRAPHIC_DATA_5="1")
    assert graphic(line,1)["points"]==[(0,0),(10,0)]
