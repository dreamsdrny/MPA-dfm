"""Coordinate discovery, import and analysis use cases."""
import os

from src.pcb_locations import component_coordinates as component_coordinates

from src.infrastructure.snapshots import capture_sources, copy_request, verify_sources


def discover_sources(*args, **options):
    from src.pick import discover_sources as discover
    return discover(*args, **options)


def analyze_and_locate(**options):
    from src.pick import analyze_and_locate as analyze
    options = copy_request(options)
    sources = capture_sources([options.get("brd_path"), *(options.get("files") or [])])
    result = analyze(**options)
    verify_sources(sources)
    return result


def import_component_coordinates(path, board_unit=""):
    from src.pick import parse_placement_txt
    from src.pcb_locations import component_coordinates
    sources = capture_sources([path])
    parsed = parse_placement_txt(path)
    if not parsed.get("ok"):
        raise ValueError(parsed.get("error", "未读到坐标"))
    coordinates = component_coordinates({"components": parsed["items"]}, board_unit or parsed.get("unit"))
    verify_sources(sources)
    return coordinates


def refresh_component_coordinates(path, directory, unit):
    from src.pick import build_inventory
    from src.pcb_locations import component_coordinates
    from src.net_locate import parse_traces_csv
    search = [directory] if directory else [os.path.join(os.path.dirname(os.path.abspath(path)), "定位清单")]
    inventory = build_inventory("" if directory else path, extra_dirs=search)
    coordinates = component_coordinates(inventory, unit)
    trace = parse_traces_csv(os.path.join(directory, "evan_traces.csv")) if directory else {}
    return coordinates, bool(inventory.get("holes") or trace.get("items"))
