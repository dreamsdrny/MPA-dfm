"""Unified MPA-DFM desktop entry point (PCB, SMT and Checklist)."""
import sys
import traceback

from evan_dfm import _crash_path, main


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        with open(_crash_path(), "w", encoding="utf-8") as handle:
            handle.write(traceback.format_exc())
        raise
