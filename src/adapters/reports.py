"""Format-specific writers, preserving existing safety and version checks."""


def export_workbench(kind, path, **data):
    from src.adapters.workbench_report import export_report
    return export_report(kind, path, **data)


def export_brd(bundle, out_dir, base_name="BRD分析报告", formats=None):
    from src.brd_analysis import write_reports
    return write_reports(bundle, out_dir, base_name=base_name, formats=formats)


def export_manufacturing(project, path):
    from src.adapters.coordination_report import export_coordination
    return export_coordination(project, path)


def preview_manufacturing(project, path, refdes=""):
    from src.adapters.coordination_report import write_coordination_preview
    return write_coordination_preview(project, path, refdes)
