"""PCB object-rule I/O; effective override policy is validated by Domain."""
import hashlib
import json

from src.adapters.pcb_configuration import _load_yaml, configuration_root
from src.domain.pcb_objects import validate_object_rules
from src.infrastructure.snapshots import capture_sources, verify_sources


def object_rules(overrides=None):
    filename = "internal/pcb/pcb_object_rules.yaml"
    path = configuration_root() / filename
    sources = capture_sources([path])
    rules, explicit = validate_object_rules(_load_yaml(filename), overrides)
    verify_sources(sources)
    effective = hashlib.sha256(json.dumps(rules, sort_keys=True, allow_nan=False).encode()).hexdigest()
    return rules, str(path), explicit, sources[0]["sha256"], effective
