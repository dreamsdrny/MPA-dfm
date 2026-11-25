"""Cadence zero-width outline/end caps must not crash PCB manufacturing analysis."""
import math
from pathlib import Path
from unittest.mock import patch

import pytest
from gerbonara.graphic_primitives import Arc, ArcPoly, Circle, Line

from src.gerber_analysis import _arc_points, _primitive_polygon, read_film, write_preview
from src.manufacturing_pipeline import analyze_brd_manufacturing
from tools.test_manufacturing_pipeline import fake_gerber, fake_run, native_fixture


HEADER = "%FSLAX46Y46*%\n%MOMM*%\n%ADD10C,0*%\nD10*\n"


@pytest.mark.parametrize("primitive", [Line(0, 0, 1, 0, 0),
    Arc(1, 0, 0, 1, 0, 0, False, 0), Circle(0, 0, 0),
    Arc(0, 0, 0, 0, 0, 0, True, 0)])
def test_zero_area_primitives_are_empty_not_division_by_zero(primitive):
    assert _primitive_polygon(primitive).is_empty


def test_zero_radius_segment_in_region_preserves_polygon():
    region = ArcPoly([(0, 0), (0, 0), (2, 0), (2, 2), (0, 2)],
                     [(True, (0, 0)), None, None, None, None])
    assert _primitive_polygon(region).area == pytest.approx(4)


@pytest.mark.parametrize("clockwise", [True, False])
def test_nonzero_radius_equal_endpoints_remain_full_circle(clockwise):
    points = _arc_points(0, 0, 1, 0, 1, 0, clockwise)
    assert len(points) > 50
    assert points[0] == points[-1] == (1, 0)
    assert min(p[0] for p in points) < -.99
    assert max(p[1] for p in points) > .99
    assert min(p[1] for p in points) < -.99
    assert (points[1][1] < 0) == clockwise


@pytest.mark.parametrize("clockwise", [True, False])
def test_ordinary_arcs_keep_direction_and_error_bound(clockwise):
    points = _arc_points(0, 0, 1, 0, 0, 1, clockwise)
    assert points[0] == (1, 0) and points[-1] == (0, 1)
    assert (points[1][1] < 0) == clockwise
    for a, b in zip(points, points[1:]):
        sagitta = 1 - math.hypot((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        assert 0 <= sagitta <= .002


def test_invalid_zero_radius_is_not_guessed_as_line():
    with pytest.raises(ValueError, match="半径为零"):
        _arc_points(0, 0, 0, 0, 1, 0, True)


@pytest.mark.parametrize("primitive", [Arc(.1, 0, 0, .1, 0, 0, False, .2),
    Arc(4.8851819999999995, 12.174474, 4.793742, 12.174474,
        4.839461999999999, 12.174474, False, .09144)])
def test_stroke_width_equal_to_diameter_has_zero_radius_inner_edge(primitive):
    # Second case is object 3724 of the customer's 894 SILK_TOP.art.
    shape = _primitive_polygon(primitive)
    assert shape.is_valid and not shape.is_empty


def test_mixed_geometry_preview_uses_compatible_svg_color_argument(tmp_path):
    from shapely.geometry import GeometryCollection, LineString, Point, Polygon
    from shapely.ops import unary_union
    area = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
    line = LineString([(2, 0), (3, 0)])
    solid = unary_union([area, line])
    assert isinstance(solid, GeometryCollection)
    path = tmp_path / "mixed_preview.html"
    bundle = dict(films=[dict(role="copper_top", geometry=solid),
                        dict(role="outline", geometry=line),
                        dict(role="mask_top", geometry=Point(4, 4))], checks=[], errors=[])
    write_preview(bundle, path)
    text = path.read_text(encoding="utf-8")
    assert "<svg" in text and "<polyline" in text and "<path" in text
    assert "#cf9446" in text and "#e0e6ef" in text


def test_zero_width_rectangular_outline_keeps_centerlines(tmp_path):
    path = tmp_path / "outline.gko"
    path.write_text(HEADER + "X0Y0D02*\nX10000000Y0D01*\n"
                    "X10000000Y10000000D01*\nX0Y10000000D01*\nX0Y0D01*\nM02*\n")
    film = read_film(path)
    assert film["geometry"].is_empty
    assert len(film["objects"]) == 4
    assert sum(obj["centerline"].length for obj in film["objects"]) == pytest.approx(40)


def test_zero_width_full_circle_outline_keeps_centerline(tmp_path):
    path = tmp_path / "outline.gko"
    path.write_text(HEADER + "G75*\nX1000000Y0D02*\nG03X1000000Y0I-1000000J0D01*\nM02*\n")
    film = read_film(path)
    assert film["geometry"].is_empty
    assert len(film["objects"]) == 1
    assert film["objects"][0]["centerline"].length == pytest.approx(2 * math.pi, abs=.01)


def _native_gerbers(board, output, editor_path, timeout, malformed=False):
    bundle = fake_gerber(board, output, editor_path, timeout)
    folder = Path(bundle["directory"])
    outline = folder / "board.gko"
    outline.write_text(HEADER + "X0Y0D02*\nX10000000Y0D01*\n"
                       "X10000000Y10000000D01*\nX0Y10000000D01*\nX0Y0D01*\nM02*\n")
    if malformed:
        broken = folder / "broken.gto"
        broken.write_text("not a valid RS-274X film")
        bundle["files"].append(str(broken))
    return bundle


@pytest.mark.parametrize("malformed", [False, True])
def test_native_workflow_keeps_geometry_and_records_bad_films(tmp_path, malformed):
    board, exe = native_fixture(tmp_path)
    def export(*args):
        return _native_gerbers(*args, malformed=malformed)
    with patch("src.gerber_export.export_brd_gerbers", side_effect=export), \
            patch("src.manufacturing_export._run", side_effect=fake_run), \
            patch("src.manufacturing_export.discover_odb_exporter", return_value=""):
        result = analyze_brd_manufacturing(board, dict(layer_count=2, copper_um=35),
                    editor_path=str(exe), out_dir=str(tmp_path / "out"))
    assert result["geometry_executed"]
    assert any(row["rule"] == "board_outline" and row["status"] == "measured"
               for row in result["geometry_coverage"])
    assert any(row["role"] == "drill" for row in result["gerber_layers"])
    assert not result["releasable"]
    if malformed:
        assert any(row["rule"] == "native_gerber_parse" and row["layer"] == "broken.gto"
                   and row["status"] == "incomplete" for row in result["geometry_coverage"])
        assert any("broken.gto" in blocker for blocker in result["release_blockers"])
