"""
BRD-AI: PCB文件解析器 (Module 1)
从Cadence Allegro .brd文件中提取关键信息
"""
import re
import os
from typing import Dict, Any, List, Optional


def extract_brd_info(brd_path: str) -> dict:
    if not os.path.exists(brd_path):
        return {"error": f"File not found: {brd_path}"}

    board_name = os.path.splitext(os.path.basename(brd_path))[0]
    info = {
        "board_name": board_name,
        "file_path": brd_path,
        "file_size_mb": round(os.path.getsize(brd_path) / (1024 * 1024), 2),
    }

    try:
        with open(brd_path, "rb") as f:
            raw = f.read()
    except Exception as e:
        info["nets"] = []
        info["error"] = f"Read error: {e}"
        return info

    text = ""
    try:
        text = raw.decode("latin-1", errors="ignore")
    except Exception:
        pass

    info["nets"] = _extract_nets(text)
    info["layers"] = _extract_layers(text)
    raw_layer_count = 0
    if not info["layers"]:
        filled = _layers_from_raw(raw)
        if filled:
            info["layers"], raw_layer_count = filled
    info["components"] = _extract_components(text)
    info["stackup"] = _extract_stackup(text)
    info["diff_pairs"] = _extract_diff_pairs_from_brd(text)
    info["power_nets"] = _extract_power_nets(info["nets"])
    info["net_count"] = len(info["nets"])
    info["layer_count"] = max(len(info["layers"]), raw_layer_count)

    return info


def _extract_nets(text: str) -> List[str]:
    nets = set()
    patterns = [
        r"NET_NAME\s*=\s*'([^']+)'",
        r'"netName"\s*:\s*"([^"]+)"',
        r"\(net\s+'([^']+)'",
        r"NET\s+'([^']+)'",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            nets.add(match.group(1))

    net_pattern = re.compile(
        r"^(?:GND|AGND|PGND|DGND|SGND|GND\d+|VCC|VDD|VIN|VBUS|"
        r"CH[0-9]+_|CLK|RESET|SPI|I2C|UART|ETH|DDR|PCIE|USB|MIPI|"
        r"LVDS|CAN|RF|ANT|LED|JTAG|GATE|SEN|DRV|PWM|"
        r"[0-9]+V|3V3|5V|12V|24V)",
        re.IGNORECASE,
    )

    binary_strings = []
    for chunk in re.finditer(rb"[A-Za-z0-9_\-\.]{3,50}", text.encode("latin-1", errors="ignore")):
        s = chunk.group(0).decode("latin-1", errors="ignore")
        if net_pattern.match(s) and not s.startswith("0") and not s.startswith("."):
            binary_strings.append(s)

    nets.update(binary_strings)
    return sorted(nets)


def _extract_layers(text: str) -> List[dict]:
    layers = []
    layer_patterns = [
        r"LAYER_NAME\s*=\s*'([^']+)'",
        r'"layerName"\s*:\s*"([^"]+)"',
        r"LAYER\s+'([^']+)'",
    ]
    found = set()
    for pattern in layer_patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            name = match.group(1)
            if name not in found:
                found.add(name)
                layers.append({"name": name, "type": "unknown"})

    common_layers = {
        "TOP": "signal", "BOTTOM": "signal",
        "L2_GND": "plane", "L3_SIG": "signal",
        "L4_GND": "plane", "L5_PWR": "plane",
        "L6_SIG": "signal", "L7_GND": "plane",
        "PASTEMASK_TOP": "mask", "PASTEMASK_BOTTOM": "mask",
        "SOLDERMASK_TOP": "mask", "SOLDERMASK_BOTTOM": "mask",
        "SILKSCREEN_TOP": "silkscreen", "SILKSCREEN_BOTTOM": "silkscreen",
    }
    for layer in layers:
        layer["type"] = common_layers.get(layer["name"], "unknown")

    return layers


def _layer_type(name: str) -> str:
    up = name.upper()
    if up.startswith("L") and "_" in up:
        kind = up.split("_", 1)[1]
        if kind in ("PWR", "PLANE", "GND"):
            return "plane"
        if kind in ("SIG", "SIGNAL", "INNER"):
            return "signal"
    return "unknown"


def _layers_from_raw(raw: bytes) -> Optional[tuple]:
    """旧版文本匹配拿不到层时, 用二进制扫描的层名与元数据补齐叠层。"""
    try:
        from src.brd_raw import scan_brd_raw
        scan = scan_brd_raw(raw)
    except Exception:
        return None

    count = int(scan.get("layer_count") or 0)
    names = list((scan.get("layers") or {}).get("names") or [])
    if not count and not names:
        return None

    def _idx(name: str) -> int:
        digits = "".join(ch for ch in name.split("_")[0] if ch.isdigit())
        return int(digits) if digits else 0

    layers: List[dict] = []
    if count:
        layers.append({"name": "TOP", "type": "signal"})
    for name in sorted(names, key=_idx):
        layers.append({"name": name, "type": _layer_type(name)})
    if count:
        layers.append({"name": "BOTTOM", "type": "signal"})
    if not layers:
        layers = [{"name": f"L{i + 1}", "type": "unknown"} for i in range(count)]

    return layers, max(count, len(layers))


def _extract_components(text: str) -> List[dict]:
    comps = []
    patterns = [
        r"COMPONENT\s*'([^']+)'\s*'([^']*)'",
        r'"ref_des"\s*:\s*"([^"]+)"',
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            comps.append({"ref": match.group(1)})
    return comps[:100]


def _extract_stackup(text: str) -> dict:
    stackup = {"layers": [], "dielectric_count": 0}
    matches = re.findall(r"DIELECTRIC\s+'([^']+)'", text, re.IGNORECASE)
    if matches:
        stackup["dielectric_count"] = len(matches)
        stackup["dielectrics"] = list(set(matches))
    return stackup


def _extract_diff_pairs_from_brd(text: str) -> List[dict]:
    pairs = []
    pattern = re.compile(r"DIFF_PAIR\s*'([^']+)'\s*'([^']+)'\s*'([^']+)'", re.IGNORECASE)
    for match in pattern.finditer(text):
        pairs.append({
            "name": match.group(1),
            "p_net": match.group(2),
            "n_net": match.group(3),
        })
    return pairs


def _extract_power_nets(nets: List[str]) -> List[str]:
    power_pattern = re.compile(
        r"^(GND|AGND|PGND|DGND|SGND|VCC|VDD|VIN|VBUS|"
        r"[0-9]+V|3V3|5V|12V|24V)",
        re.IGNORECASE,
    )
    return [n for n in nets if power_pattern.match(n)]