"""Single Excel layout contract for export and Excel's own preview/printing."""
import math
import unicodedata

from openpyxl.styles import Alignment
from openpyxl.worksheet.page import PageMargins
from openpyxl.utils import get_column_letter
from copy import copy


def display_width(text):
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in str(text))


def wrapped_lines(text, width):
    return sum(max(1, math.ceil(display_width(line)/max(width-2,4))) for line in str(text or "").split("\n"))


def format_workbook(wb):
    for ws in wb:
        picture_rows = getattr(ws, "_issue_picture_rows", [])
        # Loaded workbooks have drawing anchors but not custom Python metadata.
        loaded_pictures = bool(getattr(ws, "_images", [])) and not picture_rows
        columns = ws.max_column
        header=1
        for row in ws.iter_rows(min_row=1,max_row=min(8,ws.max_row)):
            labels={str(c.value) for c in row if c.value is not None}
            if {"序号","规则名称"}<=labels or {"序号","问题点(code)"}<=labels or {"字段","来源","置信度"}<=labels:
                header=row[0].row
        for col in range(1,columns+1):
            letter=get_column_letter(col)
            # Keep compact numerics; devote space to long descriptions/source paths.
            values=[ws.cell(r,col).value for r in range(1,ws.max_row+1) if ws.cell(r,col).value is not None]
            if not values:
                continue
            longest=max(display_width(str(v)) for v in values)
            desired=min(54,max(12,longest+2))
            existing=ws.column_dimensions[letter].width or 12
            ws.column_dimensions[letter].width=min(64,max(desired,min(existing,54)))
        # Split exceptionally long data into continuation rows instead of relying
        # on Excel's 409pt maximum height. Merged cover sections are kept intact.
        merged_rows={r for area in ws.merged_cells.ranges for r in range(area.min_row,area.max_row+1)}
        for row in range(ws.max_row,0,-1):
            if row in merged_rows or loaded_pictures:
                continue
            parts=[]
            for col in range(1,columns+1):
                value=ws.cell(row,col).value
                width=ws.column_dimensions[get_column_letter(col)].width
                capacity=max(120,int(width*12))
                chunks=[]
                if isinstance(value,str):
                    current="";units=0
                    for char in value:
                        weight=2 if unicodedata.east_asian_width(char) in ("W","F") else 1
                        if units+weight>capacity:
                            chunks.append(current);current="";units=0
                        current+=char;units+=weight
                    chunks.append(current)
                else:
                    chunks=[value]
                parts.append(chunks)
            count=max(map(len,parts),default=1)
            if count>1:
                styles=[copy(ws.cell(row,col)._style) for col in range(1,columns+1)]
                ws.insert_rows(row+1,count-1)
                picture_rows = [(image, anchor_row+count-1 if anchor_row > row else anchor_row, column)
                                for image, anchor_row, column in picture_rows]
                if row<header:header+=count-1
                for offset in range(count):
                    for col,chunks in enumerate(parts,1):
                        value=chunks[offset] if offset<len(chunks) else None
                        cell=ws.cell(row+offset,col)
                        cell.value=value
                        cell._style=copy(styles[col-1])
                        if isinstance(value,str):cell.data_type="s"
        for row in ws:
            lines=1
            for cell in row:
                cell.alignment=Alignment(horizontal=cell.alignment.horizontal or "left",vertical="top",wrap_text=True)
                if cell.value is not None:
                    lines=max(lines,wrapped_lines(cell.value,ws.column_dimensions[cell.column_letter].width))
                if isinstance(cell.value,str):cell.data_type="s"
            existing_height=ws.row_dimensions[row[0].row].height or 24
            ws.row_dimensions[row[0].row].height=min(395,max(existing_height,24,lines*15+8))
        for image, anchor_row, column in picture_rows:
            image.anchor=f"{get_column_letter(column)}{anchor_row}"
            ws.row_dimensions[anchor_row].height=max(ws.row_dimensions[anchor_row].height or 24,image.height*.75+8)
        ws._issue_picture_rows=picture_rows
        ws.freeze_panes=f"A{header+1}"
        ws.print_title_rows=f"1:{header}"
        ws.print_area=f"A1:{get_column_letter(columns)}{ws.max_row}"
        ws.sheet_properties.pageSetUpPr.fitToPage=True
        ws.page_setup.orientation="landscape" if columns>4 and not ws.title.endswith("Main") else "portrait"
        ws.page_setup.paperSize=ws.PAPERSIZE_A3 if columns>10 else ws.PAPERSIZE_A4
        ws.page_setup.fitToWidth=1
        ws.page_setup.fitToHeight=0
        ws.page_setup.scale=None
        ws.page_margins=PageMargins(left=.25,right=.25,top=.4,bottom=.4,header=.15,footer=.15)
        ws.print_options.horizontalCentered=True
        ws.oddFooter.center.text="Page &P / &N"
    # Continuation rows shift Issue detail rows; summary hyperlinks must point
    # to the actual detail sheet containing the corresponding rule code.
    issue_sheets={}
    for ws in wb:
        if "Issue" in ws.title and "summary" not in ws.title.lower():
            for row in ws:
                for cell in row:
                    if isinstance(cell.value,str):
                        import re
                        match=re.search(r"\[([^\]]+)\]",cell.value)
                        if match:issue_sheets[match[1]]=ws.title
    for ws in wb:
        if ws.title.endswith("Issue summary"):
            import re
            for row in ws:
                for cell in row:
                    if cell.hyperlink and isinstance(cell.value,str):
                        match=re.search(r"\[([^\]]+)\]",cell.value)
                        if match and match[1] in issue_sheets:cell.hyperlink=f"#'{issue_sheets[match[1]]}'!A1"
    return wb
