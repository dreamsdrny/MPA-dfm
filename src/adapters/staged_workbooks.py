"""Rewrite staged output references in Excel before publication, not in Domain."""
from openpyxl import load_workbook


def prepare_workbooks(staged, target):
    for path in staged.rglob("*.xlsx"):
        workbook = load_workbook(path)
        changed = False
        try:
            for sheet in workbook:
                for row in sheet:
                    for cell in row:
                        if isinstance(cell.value, str) and str(staged) in cell.value:
                            cell.value = cell.value.replace(str(staged), str(target))
                            cell.data_type = "s"
                            changed = True
            if changed:
                workbook.save(path)
        finally:
            workbook.close()
