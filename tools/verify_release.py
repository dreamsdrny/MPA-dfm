"""独立副本验收 EXE 授权及实际 GUI；不替换交付目录许可证。"""
import argparse
import ctypes
from ctypes import wintypes
from datetime import date, timedelta
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from src.integrity import checksums
from src.licensing import APP_ID, issue_license, write_document
from tools.release_support import protect_key


def process_tree(pid):
    """单文件 PyInstaller 的业务窗口属于子进程，不能只查启动器PID。"""
    class ProcessEntry(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("usage", wintypes.DWORD),
                    ("pid", wintypes.DWORD), ("heap", ctypes.c_size_t),
                    ("module", wintypes.DWORD), ("threads", wintypes.DWORD),
                    ("parent", wintypes.DWORD), ("priority", wintypes.LONG),
                    ("flags", wintypes.DWORD), ("exe", wintypes.WCHAR * 260)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    for name in ("Process32FirstW", "Process32NextW"):
        operation = getattr(kernel, name)
        operation.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
        operation.restype = wintypes.BOOL
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    parents = {}
    try:
        entry = ProcessEntry()
        entry.size = ctypes.sizeof(entry)
        found = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while found:
            parents[entry.pid] = entry.parent
            found = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    children = {pid}
    while True:
        expanded = children | {child for child, parent in parents.items() if parent in children}
        if expanded == children:
            return children
        children = expanded


def visible_windows(pid):
    user = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    result = []
    owned_pids = process_tree(pid)
    def collect(handle, _):
        process = wintypes.DWORD()
        user.GetWindowThreadProcessId(handle, ctypes.byref(process))
        if process.value in owned_pids and user.IsWindowVisible(handle):
            title = ctypes.create_unicode_buffer(2048)
            user.GetWindowTextW(handle, title, len(title))
            result.append((handle, title.value))
        return True
    user.EnumWindows(callback_type(collect), 0)
    return result


def gui_check(exe, folder):
    # 独立偏好目录，启动验收不加载用户上一次的BRD或修改用户偏好。
    env = dict(os.environ, APPDATA=str(folder / "gui-profile"))
    process = subprocess.Popen([str(exe)], cwd=exe.parent, env=env)
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    observed = []
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            candidates = visible_windows(process.pid)
            if any("PCB / SMT" in title for _, title in candidates):
                observed = candidates
                break
            if process.poll() is not None:
                break
            time.sleep(.2)
        assert observed, "没有进入实际 PCB / SMT 主窗口"
        assert all("test" not in title.lower() and "测试版" not in title for _, title in observed)
        # 仅记录自身窗口，避免捕获用户其他窗口或终止其他进程。
        write_document(folder / "gui.json", {"business_ui": True, "titles": [t for _, t in observed]})
        for handle, _ in observed:
            user.PostMessageW(handle, 0x0010, 0, 0)
        process.wait(timeout=30)
        assert process.returncode == 0
        return {"case": "gui", "business_ui": True, "returncode": process.returncode,
                "titles": [t for _, t in observed]}
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=15)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", required=True)
    parser.add_argument("--admin", required=True)
    parser.add_argument("--evidence", required=True)
    args = parser.parse_args()
    release, evidence = Path(args.release).resolve(), Path(args.evidence).resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    key = Ed25519PrivateKey.from_private_bytes(
        protect_key((Path(args.admin) / "issuer.dpapi").read_bytes(), decrypt=True))
    exe_source = release / "Evan_MPA_DFM.exe"
    digest = checksums(exe_source)["sha256"]
    actual = json.loads((release / "license.json").read_text(encoding="utf-8"))
    last = date.today()
    cases = {
        "valid": actual,
        "expired": issue_license(key, APP_ID, last-timedelta(days=61), last-timedelta(days=1), target_sha256=digest),
        "wrong_exe": issue_license(key, APP_ID, last, last+timedelta(days=1), target_sha256="0"*64),
        "missing": None,
    }
    tampered = json.loads(json.dumps(actual))
    tampered["payload"]["expires"] = "2099-12-31"
    cases["tampered"] = tampered
    results = []
    approved_temp = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Temp" / "opencode"
    approved_temp.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="release-verification-", dir=approved_temp) as temp:
        work = Path(temp)
        exe = work / exe_source.name
        shutil.copy2(exe_source, exe)
        for name, license_doc in cases.items():
            license_path = work / "license.json"
            if license_doc is None:
                license_path.unlink(missing_ok=True)
            else:
                write_document(license_path, license_doc)
            output = evidence / (name + ".json")
            process = subprocess.run([str(exe), "--check-deployment", str(output)], cwd=work, timeout=90)
            observed = json.loads(output.read_text(encoding="utf-8"))
            assert observed["valid"] == (name == "valid"), (name, observed)
            assert process.returncode == (0 if name == "valid" else 1)
            results.append({"case": name, "returncode": process.returncode, **observed})
        write_document(work / "license.json", actual)
        results.append(gui_check(exe, evidence))
        trace_output = evidence / "trace_ui.json"
        env = dict(os.environ, APPDATA=str(evidence / "trace-gui-profile"))
        process = subprocess.run([str(exe), "--check-trace-ui", str(trace_output)],
                                 cwd=work, env=env, timeout=90, check=False)
        trace = json.loads(trace_output.read_text(encoding="utf-8"))
        assert process.returncode == 0 and trace.get("valid"), trace
        assert "走线高亮" in trace.get("trace_title", "") and trace.get("integrated_tab"), trace
        results.append({"case": "trace_gui", "returncode": process.returncode, **trace})
        smt_output = evidence / "smt_ui.json"
        process = subprocess.run([str(exe), "--check-smt-ui", str(smt_output)],
                                 cwd=work, env=env, timeout=90, check=False)
        smt = json.loads(smt_output.read_text(encoding="utf-8"))
        assert process.returncode == 0 and smt.get("valid"), smt
        assert smt.get("manual_conditions") == 54 and smt.get("baseline_rows", 0) > 0, smt
        results.append({"case": "smt_gui", "returncode": process.returncode, **smt})
    write_document(evidence / "verification.json", {"results": results, "exe_sha256": digest})
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
