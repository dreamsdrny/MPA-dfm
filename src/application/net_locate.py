"""Net inventory, presentation selection and coordinate report use cases."""
from src.infrastructure.snapshots import capture_sources, copy_request, verify_sources


def load_net_inventory(brd_path, files=()):
    from src.net_locate import load_net_inventory as load
    sources = capture_sources([brd_path, *files])
    result = load(brd_path, files=files)
    verify_sources(sources)
    result["input_fingerprints"] = sources
    return result


def find_nets(*args, **options):
    from src.net_locate import find_nets as find
    return find(*args, **options)


def export_net_csv(net, path):
    from src.net_locate import export_net_csv as export
    from pathlib import Path
    from src.infrastructure.staging import stage_outputs
    target = Path(path).resolve()
    net = copy_request(net)
    return stage_outputs(target.parent, lambda temp: export(net, str(Path(temp) / target.name)))


def parse_traces_csv(path):
    from src.net_locate import parse_traces_csv as parse
    return parse(path)
