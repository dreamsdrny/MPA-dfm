"""BRD 文件版本与解析结果校验；独立于 GUI，供服务和工具共用。"""
import os


def content_sha256(path):
    from src.pcb_geometry_data import file_sha256
    return file_sha256(path)


def canonical_brd_path(path):
    return os.path.normcase(os.path.abspath(path)) if path else ""


def brd_signature(path):
    """路径、大小、修改时间确认读取的是当前版本，不把路径文字当作已导入。"""
    if not path:
        raise ValueError("请先导入 BRD 文件，成功读取后才能分析")
    if os.path.splitext(path)[1].lower() != ".brd":
        raise ValueError("请选择 Cadence Allegro .brd 文件")
    if not os.path.isfile(path):
        raise ValueError("BRD 文件不存在或不是文件，请重新选择")
    stat = os.stat(path)
    if stat.st_size == 0:
        raise ValueError("BRD 文件为空，无法导入")
    return canonical_brd_path(path), stat.st_size, stat.st_mtime_ns


def require_brd_signature(path, expected):
    actual = brd_signature(path)
    if not expected or tuple(actual) != tuple(expected):
        raise ValueError("BRD 文件已变化，请重新点击「从 BRD 读取」")
    return actual


def validate_brd_info(info):
    """旧格式允许部分数据，但不能把错误/空解析结果作为成功导入。"""
    if not isinstance(info, dict):
        raise ValueError("BRD 读取失败")
    if info.get("error"):
        raise ValueError(str(info["error"]))
    if not info.get("ok") and not (info.get("layers") or info.get("layer_count") or info.get("nets")):
        raise ValueError("未识别到 BRD 层或网络数据，请确认是有效的 Allegro 板文件")
    return info
