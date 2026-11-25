"""旧导入路径兼容层；版本校验实现已移到不依赖 GUI 的 src.brd_input。"""
from src.application.board_inputs import (brd_signature, canonical_brd_path,
                           require_brd_signature, validate_brd_info)

__all__ = ["brd_signature", "canonical_brd_path", "require_brd_signature", "validate_brd_info"]
