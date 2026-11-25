"""Persistent, per-user crash diagnostics without saving board contents."""
import datetime
import faulthandler
import os
import sys
import threading
import traceback
from pathlib import Path

_fault_handle = None


def log_path():
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "EvanMPA_DFM" / "logs"
    base.mkdir(parents=True, exist_ok=True)
    return base / "Evan_DFM_crash.log"


def log_exception(source, exc_type, exc, tb):
    try:
        with log_path().open("a", encoding="utf-8") as stream:
            stream.write(f"\n===== {datetime.datetime.now().isoformat(timespec='seconds')} {source} =====\n")
            stream.write("".join(traceback.format_exception(exc_type, exc, tb)))
    except OSError:
        pass


def install(root):
    global _fault_handle
    try:
        _fault_handle = log_path().open("a", encoding="utf-8")
        faulthandler.enable(_fault_handle, all_threads=True)
    except (OSError, RuntimeError):
        pass
    def callback(exc_type, exc, tb):
        log_exception("tk callback", exc_type, exc, tb)
        # The composition root/UI chooses how to render user-facing errors.
    root.report_callback_exception = callback
    def thread_hook(args):
        log_exception("thread " + args.thread.name, args.exc_type, args.exc_value, args.exc_traceback)
    threading.excepthook = thread_hook
    sys.excepthook = lambda *args: log_exception("unhandled", *args)
