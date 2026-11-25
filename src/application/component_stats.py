"""Component inventory/filter/report use cases for the auxiliary UI."""
from src.infrastructure.snapshots import capture_sources, copy_request, verify_sources


def load_component_inventory(brd_path, files=()):
    from src.component_stats import load_component_inventory as load
    sources = capture_sources([brd_path, *files])
    result = load(brd_path, files=files)
    verify_sources(sources)
    result["input_fingerprints"] = sources
    return result


def find_components(*args, **options):
    from src.component_stats import find_components as find
    return find(*args, **options)


def _export(inv, path, excel):
    from src.component_stats import export_component_csv, export_component_excel
    inv = copy_request(inv)
    verify_sources(inv.get("input_fingerprints", []))
    from pathlib import Path
    from src.infrastructure.staging import stage_outputs
    target = Path(path).resolve()
    writer = export_component_excel if excel else export_component_csv
    return stage_outputs(target.parent, lambda temp: writer(inv, str(Path(temp) / target.name)),
                         lambda: verify_sources(inv.get("input_fingerprints", [])))


def export_component_csv(inv, path):
    return _export(inv, path, False)


def export_component_excel(inv, path):
    return _export(inv, path, True)
