"""Legacy authorization API; dialog rendering remains in this compatibility entry."""
import sys
from src.infrastructure import authorization as _implementation


def check_startup(root, license_path=None):
    from tkinter import messagebox
    from src.licensing import machine_code
    try:
        return _implementation.deployment_status(license_path=license_path)
    except (OSError, ValueError) as exc:
        messagebox.showerror("启动检查失败", str(exc) + "\n机器码：" + machine_code(), parent=root)
        return None


_implementation.check_startup = check_startup
sys.modules[__name__] = _implementation
