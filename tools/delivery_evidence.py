"""Generate real Excel/PDF and UI screenshots. No production issuer/key generated."""
import argparse
import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.append(os.environ.get("EVAN_LICENSE_MANAGER_ROOT",r"D:\Evan\Softer-scret\Evan_License_Manager"))


def sample_report(output):
    from src.dfm_engine import analyze_design
    from src.workbench_report import export_report
    result=analyze_design(dict(board_name="显示验收 / Layout acceptance",min_mech_drill=.1,
        min_line_width=.05,min_spacing=.05,board_thickness=1.6))
    for issue in result.get("issues",[]):
        issue["note"]=("长文本验收：保留原点、来源、制造能力与改善建议。 Long description must wrap correctly. "*18)
    result["problem_locations"]=[dict(item="丝印到阻焊开窗",field="gerber_silk_mask",status="fail",x_mm=31.242,y_mm=6.096,
        x_brd=1230,y_brd=240,brd_unit="mil",layer="silkscreen_top",measured=.05,capability=.1524,
        note="检查距离和来源；详细说明不得截断。"*60)]
    path=output/"DFM_layout_sample.xlsx"
    export_report("pcb",path,pcb=result,board_meta=dict(board_name="显示验收板 / Acceptance board"))
    return result,path


def screenshots(output,result):
    import tkinter as tk
    from src.ui.dfm_dialog import DFMDialog
    from license_manager import LicenseManager
    from src.ui.theme import enable_dpi_awareness
    enable_dpi_awareness()
    observations=[]
    def capture(root,path):
        # PrintWindow captures our HWND, not a foreground browser/covered desktop.
        import ctypes
        import win32gui,win32ui
        hwnd=win32gui.GetAncestor(root.winfo_id(),2)
        width,height=root.winfo_width(),root.winfo_height()
        dc=win32gui.GetDC(hwnd);source=win32ui.CreateDCFromHandle(dc);target=source.CreateCompatibleDC()
        bitmap=win32ui.CreateBitmap();bitmap.CreateCompatibleBitmap(source,width,height);target.SelectObject(bitmap)
        ctypes.windll.user32.PrintWindow(hwnd,target.GetSafeHdc(),3)
        from PIL import Image
        info=bitmap.GetInfo();pixels=bitmap.GetBitmapBits(True)
        Image.frombuffer("RGB",(info['bmWidth'],info['bmHeight']),pixels,"raw","BGRX",0,1).save(path)
        win32gui.DeleteObject(bitmap.GetHandle());target.DeleteDC();source.DeleteDC();win32gui.ReleaseDC(hwnd,dc)
    for width,height,scale in [(1366,768,1.25),(1366,768,1.5),(1920,1080,1.25),(1920,1080,1.5)]:
        root=tk.Tk()
        root.tk.call("tk","scaling",96/72*scale)
        with patch("src.ui.prefs.load_prefs",return_value={}),patch("src.ui.prefs.save_prefs"):
            ui=DFMDialog(root,standalone=True)
            ui.auto_gerber.set(False)
            root.geometry(f"{width}x{height}+0+0")
            ui._render_pcb(result,{"board_name":"显示验收"})
            for _ in range(5):root.update();time.sleep(.08)
            actual=(root.winfo_width(),root.winfo_height())
            capture(root,output/f"ui_{width}x{height}_{int(scale*100)}.png")
            # Every toolbar child fits within its row. Workspace can scroll vertically/horizontally.
            from src.ui.responsive import FlowBar
            overflow=[]
            def check(widget):
                if isinstance(widget,FlowBar) and widget.winfo_ismapped():
                    for child in widget.winfo_children():
                        if child.winfo_x()+child.winfo_width()>widget.winfo_width()+2:overflow.append(child.winfo_class())
                for child in widget.winfo_children():check(child)
            check(root)
            observations.append(dict(requested=[width,height],actual=actual,tk_scale=scale,
                                      toolbar_overflow=overflow,method="Tk scaling simulation; physical Windows DPI unchanged"))
            ui._close()
    root=tk.Tk();LicenseManager(root);root.update();time.sleep(.2)
    capture(root,output/"license_manager.png")
    root.destroy()
    (output/"ui_observations.json").write_text(json.dumps(observations,indent=2),encoding="utf-8")


def excel_pdf(output,path):
    # Excel COM: actual Excel print engine, not an HTML facsimile.
    import win32com.client
    excel=win32com.client.DispatchEx("Excel.Application")
    excel.Visible=False;excel.DisplayAlerts=False
    try:
        book=excel.Workbooks.Open(str(path.resolve()))
        # Zoom is a UI preference; printing retains workbook page settings.
        book.ExportAsFixedFormat(0,str((output/"DFM_layout_sample.pdf").resolve()))
        book.Close(False)
    finally:excel.Quit()
    import pymupdf
    doc=pymupdf.open(output/"DFM_layout_sample.pdf")
    clipped=[]
    for index,page in enumerate(doc):
        for block in page.get_text("blocks"):
            if block[0]<-1 or block[1]<-1 or block[2]>page.rect.width+1 or block[3]>page.rect.height+1:
                clipped.append(index+1)
    for index in (0,5):
        if index<len(doc):doc[index].get_pixmap(matrix=pymupdf.Matrix(1.5,1.5)).save(output/f"excel_print_page_{index+1}.png")
    (output/"excel_validation.json").write_text(json.dumps(dict(excel_opened=True,pdf_pages=len(doc),
        text_outside_pages=clipped,engine="Microsoft Excel COM ExportAsFixedFormat"),indent=2),encoding="utf-8")
    doc.close()


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--out",default="delivery/evidence")
    args=parser.parse_args();output=Path(args.out).resolve();output.mkdir(parents=True,exist_ok=True)
    result,path=sample_report(output)
    screenshots(output,result)
    try:excel_pdf(output,path);print("Excel opened and exported PDF")
    except Exception as exc:(output/"excel_validation_error.txt").write_text(str(exc),encoding="utf-8");raise
    print("Evidence",output)


if __name__=="__main__":main()
