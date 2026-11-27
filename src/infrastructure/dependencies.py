"""Check importability before creating business windows, including native libs."""
from importlib import import_module

REQUIRED_MODULES = ("tkinter", "openpyxl", "yaml", "jinja2", "gerbonara", "unlzw3",
                    "shapely", "numpy", "PIL.Image", "cryptography.hazmat.primitives.asymmetric.ed25519")


def check_dependencies(modules=REQUIRED_MODULES):
    available, errors = [], []
    for name in modules:
        try:
            import_module(name)
        except (ImportError, OSError) as exc:
            errors.append(f"{name}: {exc}")
        else:
            available.append(name)
    if "shapely" in available:
        try:
            module = import_module("shapely")
            for symbol in ("STRtree", "get_coordinates", "orient_polygons"):
                if not hasattr(module, symbol):
                    errors.append(f"shapely 缺少 {symbol}，需要 2.1 或更新兼容版本")
        except (ImportError, OSError) as exc:
            errors.append(f"shapely: {exc}")
    if errors:
        raise ValueError("运行依赖缺失或无法加载：\n" + "\n".join(errors))
    return tuple(available)
