"""BRD/BOM library collaboration and version ownership."""
from src.infrastructure.snapshots import capture_sources, verify_sources


def store_paths(**options):
    from src.design_inputs.store import store_paths as paths
    return paths(**options)


def summary_lines(bundle):
    from src.design_inputs.store import summary_lines as summary
    return summary(bundle)


def run_check(root=None, brd_dir=None, bom_dir=None, log=None):
    from src.design_inputs.store import run_check as run
    paths = store_paths(root=root, brd_dir=brd_dir, bom_dir=bom_dir)
    sources = capture_sources([paths["brd"], paths["bom"]])
    result = run(root=root, brd_dir=brd_dir, bom_dir=bom_dir, log=log)
    verify_sources(sources)
    result["input_fingerprints"] = sources
    return result
