"""
BRD-AI: 信号自动分类器 (Module 2)
根据Net名称模式自动识别信号类型，映射到Net Class和Protocol
"""
import re
from dataclasses import dataclass, field
from typing import List, Dict, Optional


@dataclass
class SignalClass:
    name: str
    nets: List[str] = field(default_factory=list)
    protocol: Optional[str] = None
    is_diff_pair: bool = False
    is_power: bool = False
    is_gnd: bool = False
    width_rule: str = "default"
    spacing_rule: str = "default"
    impedance_target: Optional[float] = None
    match_tolerance: Optional[str] = None


SIGNAL_PATTERNS: Dict[str, dict] = {
    "POWER": {
        "patterns": [
            r"^(24V|12V|5V|3V3|3\.3V|1V8|1\.8V|1V2|1\.2V|1V0|1\.0V|VCC|VDD|VIN|VBUS|VPWR|VSUPPLY|V_BAT|VREG)",
            r"^(VCC_|VDD_|VIN_|VBUS_|VPWR_)",
            r"(_PWR$|_POWER$|_VCC$|_VDD$|_VIN$)",
        ],
        "width_rule": "POWER",
        "spacing_rule": "POWER",
        "is_power": True,
    },
    "GND": {
        "patterns": [
            r"^(GND|AGND|DGND|PGND|SGND|GND_|_GND)",
            r"^GND\d+$",
        ],
        "width_rule": "GND",
        "spacing_rule": "GND",
        "is_gnd": True,
    },
    "CLOCK": {
        "patterns": [
            r"^(CLK|CLOCK|OSC|XTAL|CRYSTAL)",
            r"(_CLK$|_CLOCK$|_OSC$|_XTAL$)",
        ],
        "width_rule": "CLOCK",
        "spacing_rule": "CLOCK_3W",
        "protocol": "CLOCK",
    },
    "RESET": {
        "patterns": [
            r"^(RESET|RST|POR|MR_)",
            r"(_RESET$|_RST$|_N$|_B$)",
        ],
        "width_rule": "RESET",
        "spacing_rule": "RESET_5W",
    },
    "DDR": {
        "patterns": [
            r"^(DDR|DDR_|DDR\d)",
            r"^DQ[0-9]|^DQS|^DM[0-9]",
            r"(_DQ|_DQS|_DM|_CK_P|_CK_N|_ADDR|_CMD|_CTRL)",
        ],
        "width_rule": "DDR",
        "spacing_rule": "DDR_3W",
        "protocol": "DDR",
        "is_diff_pair": True,
        "impedance_target": 100.0,
        "match_tolerance": "0.5mm",
    },
    "PCIE": {
        "patterns": [
            r"^(PCIE|PCI_E|PCI-?E)",
            r"(_PCIE|_PCI_E|_PCI-?E)",
        ],
        "width_rule": "PCIE",
        "spacing_rule": "PCIE_3W",
        "protocol": "PCIE",
        "is_diff_pair": True,
        "impedance_target": 85.0,
        "match_tolerance": "0.5mm",
    },
    "USB3": {
        "patterns": [
            r"^(USB3|USB_3|SS_TX|SS_RX)",
            r"(_USB3|_SS_TX|_SS_RX)",
        ],
        "width_rule": "USB3",
        "spacing_rule": "USB3",
        "protocol": "USB3",
        "is_diff_pair": True,
        "impedance_target": 90.0,
        "match_tolerance": "0.5mm",
    },
    "USB2": {
        "patterns": [
            r"^(USB_DP|USB_DM|USB2|USB_D)",
            r"(_USB|_USB_DP|_USB_DM)",
        ],
        "width_rule": "USB2",
        "spacing_rule": "USB2",
        "protocol": "USB2",
        "is_diff_pair": True,
        "impedance_target": 90.0,
        "match_tolerance": "0.5mm",
    },
    "MIPI": {
        "patterns": [
            r"^(MIPI|DSI|CSI)",
            r"(_MIPI|_DSI|_CSI|_CLKP|_CLKN|_D[0-9]P|_D[0-9]N)",
        ],
        "width_rule": "MIPI",
        "spacing_rule": "MIPI_3W",
        "protocol": "MIPI",
        "is_diff_pair": True,
        "impedance_target": 100.0,
        "match_tolerance": "0.5mm",
    },
    "LVDS": {
        "patterns": [
            r"^(LVDS|_LVDS)",
            r"(_LVDS|_LVDS_P|_LVDS_N)",
        ],
        "width_rule": "LVDS",
        "spacing_rule": "LVDS_5H",
        "protocol": "LVDS",
        "is_diff_pair": True,
        "impedance_target": 100.0,
        "match_tolerance": "0.5mm",
    },
    "ETHERNET": {
        "patterns": [
            r"^(ETH|ETHERNET|MDI|RGMII|RMII|SGMII|GMII|MII)",
            r"(_ETH|_MDI|_RGMII|_RMII|_SGMII|_TX|_RX)",
        ],
        "width_rule": "ETHERNET",
        "spacing_rule": "ETHERNET",
        "protocol": "ETHERNET",
        "is_diff_pair": True,
        "impedance_target": 100.0,
        "match_tolerance": "0.5mm",
    },
    "SPI": {
        "patterns": [
            r"^(SPI|MISO|MOSI|SCLK|SCK|CS_)",
            r"(_SPI|_MISO|_MOSI|_SCLK|_SCK|_CS|_SS)",
        ],
        "width_rule": "SPI",
        "spacing_rule": "SPI_3W",
        "protocol": "SPI",
    },
    "I2C": {
        "patterns": [
            r"^(I2C|SDA|SCL)",
            r"(_I2C|_SDA|_SCL)",
        ],
        "width_rule": "I2C",
        "spacing_rule": "default",
        "protocol": "I2C",
    },
    "UART": {
        "patterns": [
            r"^(UART|TX[0-9]|RX[0-9]|TXD|RXD)",
            r"(_UART|_TX|_RX|_TXD|_RXD)",
        ],
        "width_rule": "default",
        "spacing_rule": "default",
        "protocol": "UART",
    },
    "CAN": {
        "patterns": [
            r"^(CAN|CAN_|CANH|CANL)",
            r"(_CAN|_CANH|_CANL)",
        ],
        "width_rule": "CAN",
        "spacing_rule": "CAN",
        "protocol": "CAN",
        "is_diff_pair": True,
        "impedance_target": 120.0,
        "match_tolerance": "0.5mm",
    },
    "RF": {
        "patterns": [
            r"^(RF|ANT|ANTENNA|RF_|RFIN|RFOUT|FEED)",
            r"(_RF|_ANT|_ANTENNA|_FEED)",
        ],
        "width_rule": "RF",
        "spacing_rule": "RF",
        "protocol": "RF",
        "impedance_target": 50.0,
    },
    "GATE_DRIVE": {
        "patterns": [
            r"^(GATE|DRV|PWM|HSG|LSG|HDRV|LDRV|SW_)",
            r"(_GATE|_DRV|_PWM|_HSG|_LSG|_SW|_PHASE)",
        ],
        "width_rule": "GATE_DRIVE",
        "spacing_rule": "GATE_DRIVE",
    },
    "SENSE": {
        "patterns": [
            r"^(SEN|SENSE|ADC|VDET|VDETECT|ISENSE|VSENSE)",
            r"(_SEN|_SENSE|_ADC|_VDET|_VDETECT|_ISENSE|_VSENSE)",
        ],
        "width_rule": "SENSE",
        "spacing_rule": "SENSE",
    },
    "LED": {
        "patterns": [
            r"^(LED|LED_)",
            r"(_LED)",
        ],
        "width_rule": "default",
        "spacing_rule": "default",
    },
    "JTAG": {
        "patterns": [
            r"^(JTAG|TMS|TCK|TDI|TDO|TRST)",
            r"(_JTAG|_TMS|_TCK|_TDI|_TDO|_TRST)",
        ],
        "width_rule": "default",
        "spacing_rule": "default",
        "protocol": "JTAG",
    },
}


def _match_net(net_name: str, patterns: List[str]) -> bool:
    for pattern in patterns:
        if re.search(pattern, net_name, re.IGNORECASE):
            return True
    return False


def classify_signal(net_name: str) -> SignalClass:
    best_match = None
    best_priority = 999

    priority_order = [
        "GATE_DRIVE", "RF", "CAN", "MIPI", "LVDS", "PCIE",
        "USB3", "USB2", "ETHERNET", "DDR", "CLOCK", "RESET",
        "SPI", "I2C", "UART", "JTAG", "SENSE", "LED",
        "POWER", "GND",
    ]

    for sig_type, config in SIGNAL_PATTERNS.items():
        if _match_net(net_name, config["patterns"]):
            priority = priority_order.index(sig_type) if sig_type in priority_order else 999
            if priority < best_priority:
                best_priority = priority
                best_match = (sig_type, config)

    if best_match:
        sig_type, config = best_match
        return SignalClass(
            name=sig_type,
            nets=[net_name],
            protocol=config.get("protocol"),
            is_diff_pair=config.get("is_diff_pair", False),
            is_power=config.get("is_power", False),
            is_gnd=config.get("is_gnd", False),
            width_rule=config.get("width_rule", "default"),
            spacing_rule=config.get("spacing_rule", "default"),
            impedance_target=config.get("impedance_target"),
            match_tolerance=config.get("match_tolerance"),
        )
    else:
        return SignalClass(
            name="DEFAULT",
            nets=[net_name],
            width_rule="default",
            spacing_rule="default",
        )


def group_nets_by_class(net_list: List[str]) -> Dict[str, SignalClass]:
    classes: Dict[str, SignalClass] = {}
    for net_name in net_list:
        sig = classify_signal(net_name)
        if sig.name not in classes:
            classes[sig.name] = SignalClass(
                name=sig.name,
                protocol=sig.protocol,
                is_diff_pair=sig.is_diff_pair,
                is_power=sig.is_power,
                is_gnd=sig.is_gnd,
                width_rule=sig.width_rule,
                spacing_rule=sig.spacing_rule,
                impedance_target=sig.impedance_target,
                match_tolerance=sig.match_tolerance,
            )
        classes[sig.name].nets.append(net_name)
    return classes


def find_diff_pairs(net_list: List[str]) -> List[tuple]:
    pairs = []
    diff_pattern = re.compile(
        r"^(.+?)(_P|_N|_H|_L|_POS|_NEG|_PLUS|_MINUS)$", re.IGNORECASE
    )
    net_map = {}
    for net in net_list:
        m = diff_pattern.match(net)
        if m:
            base = m.group(1)
            polarity = m.group(2).upper()
            if base not in net_map:
                net_map[base] = {}
            net_map[base][polarity] = net

    for base, pols in net_map.items():
        p_candidates = [p for p in pols if p in ("_P", "_H", "_POS", "_PLUS")]
        n_candidates = [p for p in pols if p in ("_N", "_L", "_NEG", "_MINUS")]
        if p_candidates and n_candidates:
            for p in p_candidates:
                for n in n_candidates:
                    pairs.append((pols[p], pols[n]))

    return pairs


def classify_and_export(net_list: List[str]) -> dict:
    classes = group_nets_by_class(net_list)
    diff_pairs = find_diff_pairs(net_list)

    result = {
        "net_classes": {},
        "diff_pairs": [],
        "summary": {},
    }

    for cls_name, cls_obj in classes.items():
        result["net_classes"][cls_name] = {
            "nets": cls_obj.nets,
            "count": len(cls_obj.nets),
            "protocol": cls_obj.protocol,
            "is_diff_pair": cls_obj.is_diff_pair,
            "is_power": cls_obj.is_power,
            "is_gnd": cls_obj.is_gnd,
            "width_rule": cls_obj.width_rule,
            "spacing_rule": cls_obj.spacing_rule,
            "impedance_target": cls_obj.impedance_target,
            "match_tolerance": cls_obj.match_tolerance,
        }
        result["summary"][cls_name] = len(cls_obj.nets)

    result["diff_pairs"] = [{"p_net": p, "n_net": n} for p, n in diff_pairs]
    result["total_nets"] = len(net_list)
    result["total_classes"] = len(classes)
    result["total_diff_pairs"] = len(diff_pairs)

    return result