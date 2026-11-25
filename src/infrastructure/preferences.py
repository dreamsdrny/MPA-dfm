"""界面偏好持久化: 记住上次的路径与选项.

文件位置: %APPDATA%/Evan_BRD/ui_state.json (无 APPDATA 时退到用户目录)。
读写失败一律静默, 不允许因为"记不住上次设置"而影响启动或退出。
"""
import json
import os
import tempfile


def prefs_path() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "Evan_BRD", "ui_state.json")


def load_prefs() -> dict:
    """读取上次的路径与选项; 文件损坏或不存在时返回空字典。"""
    try:
        # utf-8-sig: 用户手工编辑或别的工具写文件时常带 BOM, 不能因此丢配置
        with open(prefs_path(), "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_prefs(data: dict) -> None:
    """保存界面偏好: 目录不存在时创建; 失败静默。"""
    temporary = None
    try:
        path = prefs_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=os.path.dirname(path),
                                         prefix=".prefs-", suffix=".tmp", delete=False) as f:
            temporary = f.name
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    except (OSError, TypeError, ValueError):
        pass
    finally:
        if temporary and os.path.exists(temporary):
            try:
                os.unlink(temporary)
            except OSError:
                pass
