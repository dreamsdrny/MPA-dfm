"""File readers exposed to application services, never called by domain models."""


def read_brd_design(path):
    from src.adapters.brd_reader import extract_brd_design
    return extract_brd_design(path)


def read_checklist_template(path):
    from src.adapters.checklist_reader import read_template
    return read_template(path)


def read_smt_profile(path):
    from src.adapters.smt_configuration import load_smt_profile
    return load_smt_profile(path)


def read_input(path, kind):
    """Explicit format selection; no silent fallback for unsupported formats."""
    from src.design_inputs.bom import read_bom
    from src.design_inputs.gerber_package import parse_gerber_package
    from src.design_inputs.odb import parse_odb
    readers = {"brd": read_brd_design, "bom": read_bom, "gerber": parse_gerber_package,
               "odb": parse_odb, "excel": read_checklist_template}
    if kind not in readers:
        raise ValueError("未注册的输入格式：" + kind)
    return readers[kind](path)
