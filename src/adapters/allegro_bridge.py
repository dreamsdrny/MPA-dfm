"""Open the real BRD in Allegro and update its view through a numeric mailbox.

Uses documented Allegro -s, axlUIWTimerAdd, axlMKSConvert and axlZoomPoints.
No keyboard injection, command evaluation, design edits or saves are involved.
"""
import math
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from src.allegro_skill import _short_path


BRIDGE_SKILL = r'''; Evan cross-probe: numeric millimeter coordinates only.
evan_dfm_timer = nil
evan_dfm_seq = -1
evan_dfm_design = nil
evan_dfm_request = nil
evan_dfm_ack = nil

procedure(evan_dfm_reply(seq state)
  let((fp)
    fp = outfile(evan_dfm_ack "w")
    when(fp
      fprintf(fp "%d %s\n" seq state)
      close(fp))))

procedure(evan_dfm_tick(window timer elapsed)
  prog((fp data seq x y r answer unit)
    when(axlCurrentDesign() != evan_dfm_design
      evan_dfm_reply(evan_dfm_seq "BOARD_CHANGED")
      return(nil))
    fp = infile(evan_dfm_request)
    unless(fp return(nil))
    data = errset(lineread(fp) nil)
    close(fp)
    unless(data return(nil))
    data = car(data)
    unless(length(data) == 4 return(nil))
    seq = car(data)
    unless(numberp(seq) && seq > evan_dfm_seq return(nil))
    evan_dfm_seq = seq
    unless(numberp(cadr(data)) && numberp(caddr(data)) && numberp(cadddr(data))
      evan_dfm_reply(seq "BAD_COORDINATE")
      return(nil))
    unit = car(axlDBGetDesignUnits())
    x = axlMKSConvert(cadr(data) "millimeters" unit)
    y = axlMKSConvert(caddr(data) "millimeters" unit)
    r = axlMKSConvert(cadddr(data) "millimeters" unit)
    answer = errset(axlZoomPoints(0 list(x-r y+r) list(x+r y-r)) nil)
    if(answer && car(answer)
      then evan_dfm_reply(seq "LOCATED")
      else evan_dfm_reply(seq "ZOOM_FAILED"))))

procedure(evan_dfm_connect(request ack)
  when(evan_dfm_timer axlUIWTimerRemove(evan_dfm_timer))
  evan_dfm_request = request
  evan_dfm_ack = ack
  evan_dfm_design = axlCurrentDesign()
  evan_dfm_seq = -1
  evan_dfm_timer = axlUIWTimerAdd(nil 300 nil 'evan_dfm_tick)
  if(evan_dfm_timer
    then
      evan_dfm_reply(0 "READY")
      evan_dfm_tick(nil nil 0)
    else evan_dfm_reply(0 "TIMER_FAILED")))
'''


def discover_editor(configured=""):
    """Configured path, PATH, Cadence environment, then standard installations."""
    if configured:
        path = Path(configured.strip().strip('"')).expanduser()
        return str(path.resolve()) if path.is_file() else ""
    candidates = [shutil.which("allegro.exe"), shutil.which("orcadpcb.exe")]
    for key in ("CDSROOT", "ALLEGRO_HOME", "CDS_INST_DIR"):
        base = os.environ.get(key)
        if base:
            candidates.extend(str(Path(base) / "tools" / "bin" / name)
                              for name in ("allegro.exe", "orcadpcb.exe"))
    for base in (Path("C:/Cadence"), Path("D:/Cadence"),
                 Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Cadence"):
        if base.is_dir():
            candidates.extend(str(p) for p in sorted(base.glob("*/tools/bin/allegro.exe"), reverse=True))
    return next((str(Path(p).resolve()) for p in candidates if p and Path(p).is_file()), "")


def point_mm(point):
    try:
        x, y = float(point["x_mm"]), float(point["y_mm"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("该项目没有可定位的 X/Y，请先补充对象坐标后重新分析") from None
    if not all(math.isfinite(v) for v in (x, y)):
        raise ValueError("问题坐标不是有限数值")
    return x, y


class AllegroSession:
    """One editor process and one private bridge per BRD. Closing GUI leaves CAD open."""

    def __init__(self, brd_path, editor_path, bridge_dir=None):
        self.brd_path = str(Path(brd_path).resolve())
        self.editor_path = str(Path(editor_path).resolve())
        if not Path(self.brd_path).is_file():
            raise ValueError("BRD 文件不存在：" + self.brd_path)
        if not Path(self.editor_path).is_file():
            raise ValueError("请选择有效的 Allegro PCB Editor 程序")
        self.folder = Path(bridge_dir) if bridge_dir else Path(tempfile.mkdtemp(prefix="evan-crossprobe-"))
        self.folder.mkdir(parents=True, exist_ok=True)
        self.request = self.folder / "point.dat"
        self.ack = self.folder / "point.ack"
        self.script = self.folder / "start.scr"
        self.sequence = 0
        self.process = None
        self.export_dir = ""
        self.request_time = 0.0

    def prepare(self, export_dir=""):
        il = self.folder / "evan_crossprobe.il"
        il.write_text(BRIDGE_SKILL, encoding="ascii")
        skill_path = _short_path(str(il).replace("\\", "/"))
        request = _short_path(str(self.folder).replace("\\", "/")) + "/point.dat"
        ack = _short_path(str(self.folder).replace("\\", "/")) + "/point.ack"
        lines = []
        if export_dir:
            from src.allegro_skill import write_bundle
            paths = write_bundle(export_dir)
            self.export_dir = str(Path(export_dir).resolve())
            export_il = _short_path(paths["il"].replace("\\", "/"))
            output = _short_path(self.export_dir.replace("\\", "/"))
            lines += [f'skill load("{export_il}")', f'skill evan_pick_all("{output}")']
            geometry_il = _short_path(paths["geometry_il"].replace("\\", "/"))
            lines += [f'skill load("{geometry_il}")', f'skill evan_geometry_all("{output}")']
        lines += [f'skill load("{skill_path}")', f'skill evan_dfm_connect("{request}" "{ack}")']
        encoding = "mbcs" if os.name == "nt" else "utf-8"
        self.script.write_text("\n".join(lines) + "\n", encoding=encoding, errors="strict")
        return self.script

    def launch(self):
        script = _short_path(str(self.script).replace("\\", "/"))
        # A read-only inspection session leaves the source BRD unchanged.
        self.process = subprocess.Popen([self.editor_path, "-readonly", "-s", script, self.brd_path],
                                        cwd=str(self.folder))
        return self.process.pid

    def locate(self, point, radius_mm=2.5):
        x, y = point_mm(point)
        radius = float(radius_mm)
        if not math.isfinite(radius) or radius <= 0:
            raise ValueError("定位视图半径必须大于 0")
        self.sequence += 1
        temporary = self.folder / "point.tmp"
        temporary.write_text(f"{self.sequence} {x:.9f} {y:.9f} {radius:.9f}\n", encoding="ascii")
        os.replace(temporary, self.request)
        self.request_time = time.monotonic()
        return self.sequence

    def status(self):
        try:
            text = self.ack.read_text(encoding="ascii").split()
            return int(text[0]), text[1]
        except (OSError, ValueError, IndexError):
            return None, "WAITING"

    def running(self):
        return self.process is not None and self.process.poll() is None


def arrange_editor(pid, app_hwnd):
    """Arrange only the launched editor process and this workbench side by side."""
    if os.name != "nt":
        return False
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.MoveWindow.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, wintypes.BOOL]
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
    windows = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback_type
    def collect(hwnd, _):
        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        if process_id.value == pid and user32.IsWindowVisible(hwnd):
            title = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, title, 512)
            if "allegro" in title.value.lower() or "orcad" in title.value.lower():
                windows.append(hwnd)
        return True

    user32.EnumWindows(collect, 0)
    if not windows:
        return False
    rect = wintypes.RECT()
    if not user32.SystemParametersInfoW(48, 0, ctypes.byref(rect), 0):
        return False
    width, height = rect.right - rect.left, rect.bottom - rect.top
    app = user32.GetAncestor(app_hwnd, 2) or app_hwnd
    for hwnd, left, size in ((app, rect.left, width // 2),
                             (windows[0], rect.left + width // 2, width - width // 2)):
        user32.ShowWindow(hwnd, 9)
        user32.MoveWindow(hwnd, left, rect.top, size, height, True)
    return True
