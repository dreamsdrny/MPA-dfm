"""分析上下文: 一块板的所有可用数据只加载一次, 供各项能力共用。

一次分析要跑 40 多项能力, 如果每项都重新读一遍 20MB 的 .brd、重新解一次
快照 JSON、重新扫一遍二进制, 会慢得没法用。Context 负责:

    1. 只读一次 .brd (交给 src.brd_design.extract_brd_design, 它已经做了单位判定/合理性校验)
    2. 只解一次内嵌快照 (交给 src.design_geometry.load_design_snapshot)
    3. 按需缓存几何 (normalize 之后的毫米线段), 按 kind 分组
    4. 解析用户提供的 Allegro 导出文件 (DRC 报告 / Placement / Excellon / ...)

并对外暴露一组"有没有这个数据"的判定 (has_snapshot / has_scale / has_xy ...),
让每项能力自己决定是出结论还是如实写"缺什么"。
"""
import os
from typing import Any, Dict, Iterable, List, Optional

from src.brd_analysis import geom


class Context:
    """一次 .brd 分析的全部输入与派生数据。"""

    def __init__(self, brd_path: str, out_dir: str = "",
                 export_files: Optional[Iterable[str]] = None,
                 extra_dirs: Iterable[str] = ()):
        self.brd_path = os.path.abspath(brd_path) if brd_path else ""
        self.out_dir = out_dir or ""
        self.export_files = [p for p in (export_files or []) if p]
        self.extra_dirs = [d for d in (extra_dirs or []) if d]

        self._design: Optional[dict] = None
        self._design_error: Optional[str] = None
        self._snapshot = None
        self._snapshot_loaded = False
        self._scale: Optional[float] = None
        self._geometry: Optional[dict] = None
        self._seg_cache: Dict[tuple, List[dict]] = {}
        self._exports: Optional[dict] = None
        self._objects: Optional[dict] = None
        self._notes: List[str] = []
        # 能力之间共享数据的暂存区 (例如 report.* 复用它项已算好的行);
        # 不保证写入顺序, 读的一方必须能接受"里面是空的"。
        self.shared: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # 基础信息
    # ------------------------------------------------------------------
    @property
    def board_name(self) -> str:
        if self.design.get("design_name"):
            return self.design["design_name"]
        return os.path.splitext(os.path.basename(self.brd_path))[0] or "(未命名)"

    @property
    def design(self) -> dict:
        """src.brd_design.extract_brd_design 的结果 (约束/网络/器件/钻孔/板框)。"""
        if self._design is None:
            if not self.brd_path or not os.path.exists(self.brd_path):
                self._design = {"ok": False, "error": f"文件不存在: {self.brd_path}"}
            else:
                try:
                    from src.brd_design import extract_brd_design

                    self._design = extract_brd_design(self.brd_path)
                except Exception as exc:  # noqa: BLE001  解析失败必须降级而不是崩
                    self._design = {"ok": False, "error": f"BRD 解析失败: {exc}"}
            if not self._design.get("ok"):
                self._design_error = self._design.get("error") or "BRD 解析失败"
        return self._design

    @property
    def design_ok(self) -> bool:
        return bool(self.design.get("ok"))

    @property
    def design_error(self) -> Optional[str]:
        _ = self.design
        return self._design_error

    @property
    def snapshot(self):
        """内嵌设计快照 JSON (design.comps 里是全板图形); 没有则 None。"""
        if not self._snapshot_loaded:
            self._snapshot_loaded = True
            if self.brd_path and os.path.exists(self.brd_path):
                try:
                    from src.design_geometry import load_design_snapshot

                    self._snapshot = load_design_snapshot(self.brd_path)
                except Exception:  # noqa: BLE001
                    self._snapshot = None
        return self._snapshot

    @property
    def has_snapshot(self) -> bool:
        return self.snapshot is not None

    @property
    def raw_entries(self) -> List[dict]:
        from src.design_geometry import _entries

        return _entries(self.snapshot or {})

    @property
    def scale_mm_per_unit(self) -> float:
        """快照坐标 -> mm 的换算系数。取不到时返回 0 (调用方必须判 0)。"""
        if self._scale is None:
            self._scale = 0.0
            geo = self.board_geometry
            outline = (geo or {}).get("outline") or {}
            if outline.get("ok"):
                try:
                    self._scale = float(outline.get("scale_mm_per_unit") or 0.0)
                except (TypeError, ValueError):
                    self._scale = 0.0
        return self._scale

    @property
    def board_geometry(self) -> dict:
        if self._geometry is None:
            geo = {"ok": False, "reason": "未读取板框"}
            if self.brd_path and os.path.exists(self.brd_path):
                try:
                    from src.design_geometry import board_geometry

                    qv = (self.design.get("raw_note") or {}).get("quickview") or {}
                    geo = board_geometry(self.brd_path, qv if qv.get("found") else None)
                except Exception as exc:  # noqa: BLE001
                    geo = {"ok": False, "reason": f"板框解析失败: {exc}"}
            self._geometry = geo
        return self._geometry

    @property
    def outline(self) -> dict:
        return (self.board_geometry or {}).get("outline") or {}

    @property
    def has_scale(self) -> bool:
        return self.scale_mm_per_unit > 0

    @property
    def scale_confident(self) -> bool:
        return self.outline.get("method") == "draw_size"

    # ------------------------------------------------------------------
    # 几何
    # ------------------------------------------------------------------
    def segments(self, kinds: Optional[Iterable[str]] = None) -> List[dict]:
        """毫米坐标的线段; kinds=None 表示全部 (按类别缓存)。"""
        key = tuple(sorted(kinds)) if kinds else ("*",)
        if key not in self._seg_cache:
            if not self.has_snapshot or not self.has_scale:
                self._seg_cache[key] = []
            else:
                try:
                    self._seg_cache[key] = geom.normalize(
                        self.raw_entries, self.scale_mm_per_unit, kinds)
                except Exception:  # noqa: BLE001
                    self._seg_cache[key] = []
        return self._seg_cache[key]

    def segments_by_layer(self, kinds: Iterable[str]) -> Dict[str, List[dict]]:
        out: Dict[str, List[dict]] = {}
        for seg in self.segments(kinds):
            out.setdefault(seg["layer"], []).append(seg)
        return out

    @property
    def layer_count(self) -> Optional[int]:
        value = self.design.get("layer_count")
        if value:
            return int(value)
        layers = self.design.get("layers") or []
        return len(layers) or None

    @property
    def layers(self) -> List[str]:
        return list(self.design.get("layers") or [])

    @property
    def etch_layers(self) -> List[str]:
        """快照里真实有铜箔图形的层 (比 SubClass.xml 的层表更可信地反映"这层有东西")。"""
        return sorted({s["layer"] for s in self.segments(["etch"])})

    # ------------------------------------------------------------------
    # Allegro 导出文件
    # ------------------------------------------------------------------
    @property
    def exports(self) -> dict:
        """解析出来的外部导出数据 (坐标清单 / 钻孔 / DRC 报告 / 网表 ...)。"""
        if self._exports is None:
            try:
                from src.brd_analysis.imports import load_exports

                self._exports = load_exports(self.brd_path, self.export_files,
                                             self.extra_dirs)
            except Exception as exc:  # noqa: BLE001
                self._exports = {"ok": False, "error": str(exc), "kinds": {},
                                 "files": [], "components": [], "holes": [], "pads": [],
                                 "drc": None, "netlist": None, "placement": None}
        return self._exports

    def exports_of(self, kind: str) -> list:
        return [f for f in (self.exports.get("files") or []) if f.get("kind") == kind]

    @property
    def has_components_xy(self) -> bool:
        return bool(self.exports.get("components"))

    @property
    def has_holes_xy(self) -> bool:
        return bool(self.exports.get("holes"))

    @property
    def has_pads_xy(self) -> bool:
        """是否导入了逐引脚的焊盘几何 (SKILL evan_pads.csv)。"""
        return bool(self.exports.get("pads"))

    # ------------------------------------------------------------------
    # .brd 归档原始对象 (网络名/差分对名/子类表这类细节从 XML 节点直接读)
    # ------------------------------------------------------------------
    @property
    def objects(self) -> Dict[str, bytes]:
        """zip 归档里的 XML 对象 {条目名: 内容}; 读不到返回空字典。"""
        if self._objects is None:
            self._objects = {}
            if self.brd_path and os.path.exists(self.brd_path):
                try:
                    from src.brd_design import read_brd_objects

                    objects, _info = read_brd_objects(self.brd_path)
                    self._objects = objects
                except Exception:  # noqa: BLE001
                    self._objects = {}
        return self._objects

    def xml_nodes(self, entry: str) -> List[str]:
        """读某个归档条目下所有 <o N="..."> 的名字 (Net.xml / DiffPair.xml 等)。"""
        from xml.etree import ElementTree as ET

        data = self.objects.get(entry)
        if not data:
            return []
        try:
            root = ET.fromstring(data)
        except ET.ParseError:
            return []
        out = []
        for node in root.findall("o"):
            name = (node.get("N") or "").strip()
            if name:
                out.append(name)
        return out

    # ------------------------------------------------------------------
    # 常用"怎么补数据"的说明 (多个能力共用, 保证口径一致)
    # ------------------------------------------------------------------
    def howto_components_xy(self) -> str:
        return ("Allegro: File > Export > Placement 导出 place_txt.txt, "
                "或用工具生成的 SKILL (--export-skill <目录>) 导出 evan_components.csv")

    def howto_holes_xy(self) -> str:
        return ("Allegro: Manufacture > NC > NC Drill 导出 Excellon (.drl), "
                "或用 SKILL 导出 evan_holes.csv")

    def howto_drc_report(self) -> str:
        return ("Allegro: Tools > Quick Reports / Display > Status 或 "
                "Manufacture > DRC 跑一次, 把 DRC 报告 (.rpt) 一并提供给工具")

    def howto_stackup(self) -> str:
        return ("Allegro: Setup > Cross-section 打开叠层, 导出层叠报告 (含介质厚度 Er), "
                "或提供板厂的叠层表")

    def note(self, text: str) -> None:
        if text and text not in self._notes:
            self._notes.append(text)

    @property
    def notes(self) -> List[str]:
        return list(self._notes)

    # ------------------------------------------------------------------
    def summary(self) -> dict:
        d = self.design
        return {
            "brd_path": self.brd_path,
            "board_name": self.board_name,
            "design_ok": self.design_ok,
            "design_error": self.design_error,
            "file_size_mb": d.get("file_size_mb"),
            "layer_count": self.layer_count,
            "net_count": d.get("net_count"),
            "part_count": d.get("part_count"),
            "diff_pair_count": d.get("diff_pair_count"),
            "has_snapshot": self.has_snapshot,
            "snapshot_entries": len(self.raw_entries) if self.has_snapshot else 0,
            "scale_mm_per_unit": self.scale_mm_per_unit,
            "scale_method": self.outline.get("method"),
            "board_size_mm": d.get("board_size_mm"),
            "export_files": len(self.exports.get("files") or []),
            "has_components_xy": self.has_components_xy,
            "has_holes_xy": self.has_holes_xy,
            "has_pad_geometry": self.has_pads_xy,
            "warnings": list(d.get("warnings") or []) + self.notes,
        }


def load(brd_path: str, out_dir: str = "", export_files: Optional[Iterable[str]] = None,
         extra_dirs: Iterable[str] = ()) -> Context:
    return Context(brd_path, out_dir=out_dir, export_files=export_files,
                   extra_dirs=extra_dirs)
