"""Cross-probe contract: launch args, unit conversion, reuse and numeric requests."""
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.allegro_bridge import AllegroSession, BRIDGE_SKILL, discover_editor, point_mm
from src.pick import parse_skill_csv
from src.pcb_locations import component_coordinates


class BridgeTests(unittest.TestCase):
    def test_launch_and_latest_numeric_request(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            board = root / "board with spaces.brd"
            editor = root / "allegro.exe"
            board.touch()
            editor.touch()
            session = AllegroSession(board, editor, root / "bridge")
            session.prepare()
            with patch("src.allegro_bridge.subprocess.Popen") as launch:
                launch.return_value.pid = 123
                session.launch()
                args = launch.call_args.args[0]
                self.assertEqual(args[1:3], ["-readonly", "-s"])
                self.assertEqual(args[-1], str(board.resolve()))
            session.locate({"x_mm": -2.54, "y_mm": 0})
            self.assertEqual(session.request.read_text().split(), ["1", "-2.540000000", "0.000000000", "2.500000000"])
            session.locate({"x_mm": 10, "y_mm": 20})
            self.assertEqual(session.request.read_text().split()[0], "2")
            session.ack.write_text("2 LOCATED\n")
            self.assertEqual(session.status(), (2, "LOCATED"))
            self.assertIn('car(axlDBGetDesignUnits())', BRIDGE_SKILL)
            self.assertIn("axlZoomPoints", BRIDGE_SKILL)

    def test_invalid_location_rejected_and_configured_editor(self):
        for point in ({}, {"x_mm": math.nan, "y_mm": 1}, {"x_mm": 0, "y_mm": math.inf}):
            with self.assertRaises(ValueError):
                point_mm(point)
        self.assertEqual(discover_editor("does-not-exist.exe"), "")

    def test_padstack_actual_drill_users_units(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "evan_holes.csv"
            path.write_text("UNITS,mils,3,1000\n"
                            "KIND,PADSTACK,X_UU,Y_UU,NET,REFDES,DRILL_UU,IS_THROUGH\n"
                            "VIA,custom_pad,100,200,GND,,5,YES\n")
            hole = parse_skill_csv(str(path))["items"][0]
            self.assertAlmostEqual(hole["drill_mm"], 0.127)
            self.assertTrue(hole["is_through"])
            self.assertIn("drillDiameter", hole["drill_source"])

    def test_component_true_origin_zero_and_negative_units(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "evan_components.csv"
            path.write_text("UNITS,mils,2,100\nREFDES,X_UU,Y_UU,ROTATION,SIDE\n"
                            "U1,-100,0,90.0,TOP\nR1,210,240,0.0,BOTTOM\n")
            inventory = {"components": parse_skill_csv(str(path))["items"]}
            coordinates = component_coordinates(inventory, "mils")
            self.assertEqual((coordinates[0]["x_brd"], coordinates[0]["y_brd"]), (-100, 0))
            self.assertEqual(coordinates[0]["command"], "x -100.000000 0.000000")
            self.assertEqual(coordinates[1]["side"], "BOTTOM")
            self.assertAlmostEqual(coordinates[1]["x_mm"], 5.334)


if __name__ == "__main__":
    unittest.main()
