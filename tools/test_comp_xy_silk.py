import tempfile
import unittest
from pathlib import Path

from src.pick import parse_placement_txt, build_inventory
from src.pcb_locations import component_coordinates, attach_locations
from src.silk_position import check_positions, parse_geometry

SAMPLE = """VERSION = 2.0
UUNITS = MILS
# refdes ! symbol_x ! symbol_y ! rotation ! mirror ! symbol_name ! embedded_layer
R1 ! 1239.66 ! 4255.44 ! 270 ! m ! RESC1608X95N_NTC !
J2 ! 1368.42 ! 4266.49 ! 0 ! ! E2_VM_PAD_ON_FPC !
J3 ! 1368.35 ! 4061.77 ! 0 ! ! E2_VM_PAD_ON_FPC !
J1 ! 1298.87 ! 8445.33 ! 352.424 ! ! FH63S-10S-05SH-FPC !
"""


def geometry(kind="SILK", type="line", x1=1, y1=0.05, x2=2, y2=0.05, width=0.05, layer="PACKAGE GEOMETRY/SILKSCREEN_TOP"):
    return dict(kind=kind, type=type, x_mm=(x1+x2)/2, y_mm=(y1+y2)/2, width_mm=width,
                xmin_mm=min(x1,x2), ymin_mm=min(y1,y2), xmax_mm=max(x1,x2), ymax_mm=max(y1,y2),
                x1_mm=x1, y1_mm=y1, x2_mm=x2, y2_mm=y2, layer=layer, source="test.csv")


class CompSilkTests(unittest.TestCase):
    def test_user_comp_xy_format_and_mirror(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "COMP_XY_S-860-000847_Rev_A.txt"
            path.write_text(SAMPLE)
            parsed = parse_placement_txt(str(path))
            self.assertTrue(parsed["ok"])
            self.assertEqual(len(parsed["items"]), 4)
            self.assertEqual(parsed["items"][0]["side"], "BOTTOM")
            self.assertAlmostEqual(parsed["items"][0]["x_mm"], 31.487364)
            inventory = build_inventory("", extra_dirs=[folder])
            self.assertEqual(len(inventory["components"]), 4)
            self.assertEqual(component_coordinates(inventory, "mil")[0]["x_brd"], 1239.66)

    def test_silk_width_and_edge_actual_geometry(self):
        silk = geometry()
        outline = geometry("OUTLINE", x1=0,y1=0,x2=10,y2=0,width=0,layer="BOARD GEOMETRY/OUTLINE")
        capability = {"silkscreen": {"min_line_width": {"value":0.1,"unit":"mm"},
                                     "min_to_outline": {"value":0.2,"unit":"mm"}}}
        checks = check_positions([silk], [outline], [], capability)
        self.assertEqual([r["status"] for r in checks], ["fail", "fail"])
        self.assertAlmostEqual(checks[1]["measured"], 0.025)
        self.assertEqual(checks[1]["x_mm"], 1.5)
        # Same component XY alone cannot invent any silkscreen issue.
        self.assertEqual(check_positions([], [], [], capability, [{"x_mm":1,"y_mm":2}]), [])

    def test_bottom_pads_do_not_trigger_top_silk_and_text_is_review(self):
        silk = geometry(type="text",width=0)
        pad = dict(x_mm=1.5,y_mm=0.05,w_mm=0.4,h_mm=0.4,side="BOTTOM",refdes="U1")
        capability = {"silkscreen": {"min_to_solder_mask":{"value":0.1,"unit":"mm"}}}
        self.assertEqual(check_positions([silk], [], [pad], capability), [])
        pad["side"] = "TOP"
        self.assertEqual(check_positions([silk], [], [pad], capability)[0]["status"], "warn_limit")

    def test_position_violation_changes_manufacturing_verdict(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "evan_silkscreen.csv"
            path.write_text("UNITS,mm\nKIND,TYPE,LAYER,REFDES,X_UU,Y_UU,WIDTH_UU,XMIN_UU,YMIN_UU,XMAX_UU,YMAX_UU,X1_UU,Y1_UU,X2_UU,Y2_UU\n"
                "SILK,line,PACKAGE GEOMETRY/SILKSCREEN_TOP,R1,1,2,0.01,0,2,2,2,0,2,2,2\n")
            result = attach_locations(dict(items=[],issues=[],factory="hongban",board_type="rigid",
                capability_level="standard",passed=True,releasable=True,verdict="通过"),extra_dirs=[folder],design_unit="mm")
            self.assertFalse(result["passed"])
            self.assertFalse(result["releasable"])
            self.assertEqual(result["error_count"], 1)
            self.assertEqual(result["problem_locations"][0]["x_brd"], 1)


if __name__ == "__main__":
    unittest.main()
