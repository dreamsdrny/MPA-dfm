import tempfile
import unittest
from pathlib import Path

from src.ui.brd_input import brd_signature, require_brd_signature, validate_brd_info


class BrdInputTests(unittest.TestCase):
    def test_signature_detects_missing_and_changed_board(self):
        with tempfile.TemporaryDirectory() as directory:
            board = Path(directory)/"board.brd"
            board.write_bytes(b"board-v1")
            signature = brd_signature(str(board))
            self.assertEqual(require_brd_signature(str(board), signature), signature)
            board.write_bytes(b"board-version-2")
            with self.assertRaises(ValueError):
                require_brd_signature(str(board), signature)
        with self.assertRaises(ValueError):
            require_brd_signature(str(board), signature)

    def test_failed_and_empty_parse_not_accepted(self):
        for info in (None, {}, {"ok": False}, {"error": "bad BRD"}):
            with self.subTest(info=info), self.assertRaises(ValueError):
                validate_brd_info(info)
        self.assertEqual(validate_brd_info({"ok": True}), {"ok": True})
        self.assertEqual(validate_brd_info({"_legacy": True, "layer_count": 2})["layer_count"], 2)


if __name__ == "__main__":
    unittest.main()
