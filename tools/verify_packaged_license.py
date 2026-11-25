"""Validate test EXE: signed valid license enters UI; expired/tampered deny entry."""
import json
import shutil
import subprocess
import time
from pathlib import Path

import win32gui,win32con,win32ui
import ctypes
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
FOLDER=ROOT/"delivery"/"test_app"
EVIDENCE=ROOT/"delivery"/"evidence"
SOURCE=Path(r"C:\Users\SZV01201\AppData\Local\Temp\opencode\evan-license-test")


def windows():
    result=[]
    def collect(hwnd,_):
        if win32gui.IsWindowVisible(hwnd):
            title=win32gui.GetWindowText(hwnd)
            if "Evan" in title or "License invalid" in title:
                result.append((hwnd,title))
    win32gui.EnumWindows(collect,None)
    return result


def capture(hwnd,path):
    left,top,right,bottom=win32gui.GetWindowRect(hwnd)
    dc=win32gui.GetWindowDC(hwnd);source=win32ui.CreateDCFromHandle(dc);target=source.CreateCompatibleDC()
    bitmap=win32ui.CreateBitmap();bitmap.CreateCompatibleBitmap(source,right-left,bottom-top);target.SelectObject(bitmap)
    ctypes.windll.user32.PrintWindow(hwnd,target.GetSafeHdc(),2)
    Image.frombuffer("RGB",(right-left,bottom-top),bitmap.GetBitmapBits(True),"raw","BGRX",0,1).save(path)
    win32gui.DeleteObject(bitmap.GetHandle());target.DeleteDC();source.DeleteDC();win32gui.ReleaseDC(hwnd,dc)


def run_case(name,license):
    shutil.copy2(license,FOLDER/"license.json")
    existing={hwnd for hwnd,_ in windows()}
    p=subprocess.Popen([str(FOLDER/"Evan_MPA_DFM_License_TEST.exe")],cwd=FOLDER)
    deadline=time.monotonic()+20
    found=[]
    while time.monotonic()<deadline:
        found=[(hwnd,title) for hwnd,title in windows() if hwnd not in existing]
        if found:break
        time.sleep(.3)
    business=any("PCB / SMT" in title for _,title in found)
    assert business==(name=="valid"),(name,found)
    if not found:raise AssertionError("No UI or license error dialog")
    for hwnd,title in found:
        capture(hwnd,EVIDENCE/f"license_{name}.png")
        # Send OK only to the test application's refusal dialog, then close UI.
        if "License invalid" in title:
            button=win32gui.FindWindowEx(hwnd,0,"Button",None)
            if button:win32gui.SendMessage(button,win32con.BM_CLICK,0,0)
        else:win32gui.PostMessage(hwnd,win32con.WM_CLOSE,0,0)
    p.wait(timeout=15)
    return dict(case=name,business_ui=business,returncode=p.returncode,titles=[t for _,t in found])


def main():
    EVIDENCE.mkdir(parents=True,exist_ok=True)
    results=[run_case("valid",SOURCE/"license.json"),run_case("expired",SOURCE/"expired_license.json")]
    tampered=SOURCE/"tampered_license.json"
    doc=json.loads((SOURCE/"license.json").read_text());doc["payload"]["expires"]="2099-12-31"
    tampered.write_text(json.dumps(doc))
    results.append(run_case("tampered",tampered))
    shutil.copy2(SOURCE/"license.json",FOLDER/"license.json")
    shutil.copy2(SOURCE/"expired_license.json",FOLDER/"expired_license.json")
    (EVIDENCE/"packaged_license_results.json").write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding="utf-8")
    print(results)


if __name__=="__main__":main()
