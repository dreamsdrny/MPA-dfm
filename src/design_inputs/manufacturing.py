"""制造资料适配器：ODB++ ASCII 子集与 STEP 文件头识别。"""
from src.adapters.step_reader import parse_step as parse_step


def parse_odb(*args, **kwargs):
    # Lazy compatibility boundary avoids odb_reader -> design_inputs package
    # initialization -> manufacturing -> odb_reader circular imports.
    from src.adapters.odb_reader import parse_odb as read
    return read(*args, **kwargs)
