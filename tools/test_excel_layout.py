import tempfile
import unittest
from pathlib import Path
from openpyxl import Workbook,load_workbook
from src.excel_layout import format_workbook


class ExcelLayoutTests(unittest.TestCase):
    def test_wrapping_continuation_print_and_full_text(self):
        wb=Workbook();ws=wb.active
        ws.append(["编号","长说明"])
        text="含中英文的长文本 Long description "*300
        ws.append([1,text]);ws.append(["合计",1])
        format_workbook(wb)
        recovered="".join(str(ws.cell(row,2).value or "") for row in range(2,ws.max_row))
        self.assertEqual(recovered,text)
        self.assertGreater(ws.max_row,3)
        self.assertTrue(ws["B2"].alignment.wrap_text)
        self.assertEqual(ws.page_setup.fitToWidth,1)
        self.assertEqual(ws.page_setup.fitToHeight,0)
        self.assertEqual(ws.print_title_rows,"$1:$1")
        self.assertLessEqual(max(dim.height for dim in ws.row_dimensions.values()),395)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"layout.xlsx";wb.save(path)
            restored=load_workbook(path)
            self.assertEqual(restored.active.freeze_panes,"A2")
            restored.close()

    def test_short_cells_not_duplicated_in_continuation_rows(self):
        wb=Workbook();ws=wb.active
        ws.append(["id","description"]);ws.append([1,"说明"*2000]);ws.append(["total",1])
        format_workbook(wb)
        self.assertEqual(sum(ws.cell(r,1).value==1 for r in range(2,ws.max_row)),1)


if __name__=="__main__":unittest.main()
