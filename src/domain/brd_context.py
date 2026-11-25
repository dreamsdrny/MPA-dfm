"""Structural contract for supplied BRD evidence; Domain never constructs readers."""
from typing import Protocol


class Context(Protocol):
    """Application prepares data; capability rules consume this evidence view."""
    design: dict
    exports: dict
    objects: dict
    snapshot: dict
    shared: dict
    scale_confident: bool

    def segments(self, kinds=(), layers=()): ...
    def xml_nodes(self, entry): ...
    def note(self, text): ...
