"""分析与输出能力的统一数据模型。

设计口径
--------
本包把用户口中的「PCB Editor 分析与输出功能」拆成 **8 大类 / 若干能力 (capability)**,
每项能力都是一次「分析 -> 结论 -> 产物」的调用:

    Capability   能力定义 (id / 类别 / 需要什么数据 / 产出什么 / 怎么算)
    Result       能力的运行结果 (状态 / 指标 / 明细 / 缺失说明 / 产物)

为什么要有统一模型:
  1. 报告生成、界面勾选、命令行 `--analyze` 都读同一份结果, 不各写一套;
  2. 「缺数据 ≠ 通过」——数据不足的能力状态是 `unavailable` 且必须写清
     `missing`(缺什么) 与 `howto`(怎么补), 不允许伪造成 pass;
  3. 结论可追溯: 每条指标都带 source(出处) 与 confidence(置信度)。
"""
from typing import Any, Callable, Dict, List, Optional
import re

# --------------------------------------------------------------------------
# 8 大类
# --------------------------------------------------------------------------
CATEGORIES: List[tuple] = [
    ("drc", "设计规则检查 (DRC)"),
    ("physical", "PCB 物理结构分析"),
    ("electrical", "信号完整性 (SI) 与电气分析"),
    ("manufacturing", "制造文件输出 (Manufacturing Output)"),
    ("reports", "PCB 报告输出 (Reports)"),
    ("assembly", "SMT 贴装与装配分析"),
    ("mcad", "机械结构与 ECAD/MCAD 数据交换"),
    ("database", "数据库与设计数据导出"),
]

CATEGORY_LABELS: Dict[str, str] = dict(CATEGORIES)
CATEGORY_ORDER: List[str] = [key for key, _ in CATEGORIES]

# --------------------------------------------------------------------------
# 结果状态
# --------------------------------------------------------------------------
PASS = "pass"            # 已判定, 符合
FAIL = "fail"            # 已判定, 不符合
WARN = "warn"            # 已判定, 需关注
INFO = "info"            # 仅统计/信息, 无判定口径
UNAVAILABLE = "unavailable"   # 数据不足, 未做判定 (缺什么写 missing)
SKIPPED = "skipped"      # 本次未选中/不适用

STATUS_LABELS: Dict[str, str] = {
    PASS: "通过",
    FAIL: "不符合",
    WARN: "需关注",
    INFO: "信息",
    UNAVAILABLE: "数据不足",
    SKIPPED: "未运行",
}

# 与 src/dfm_rules 的严重度配色保持同一观感 (Excel 用 RRGGBB)
STATUS_COLORS: Dict[str, str] = {
    PASS: "1E7B34",
    FAIL: "C00000",
    WARN: "BF8F00",
    INFO: "2F5496",
    UNAVAILABLE: "808080",
    SKIPPED: "A6A6A6",
}

# 数据来源引擎: 决定这项能力"靠什么算出来"
ENGINE_LABELS: Dict[str, str] = {
    "brd": "BRD 二进制/归档直读",
    "snapshot": "BRD 内嵌设计快照几何",
    "export": "Allegro 导出文件",
    "external": "需 Allegro/Sigrity 授权模块",
}


class Capability:
    """一项分析/输出能力。

    参数
    ----
    id        全局唯一, 形如 "drc.width"; 命令行 `--analyze-only drc.width` 用它
    category  8 大类之一
    name      英文名 (对齐 PCB Editor 的功能名)
    name_cn   中文名
    desc      这项能力回答什么问题
    engine    见 ENGINE_LABELS; "external" 表示本工具无法独立完成
    needs     需要哪些数据 (人类可读)
    outputs   会产出什么 (人类可读)
    func      处理函数, 签名 func(ctx) -> Result
    """

    __slots__ = ("id", "category", "name", "name_cn", "desc", "engine",
                 "needs", "outputs", "func")

    def __init__(self, id: str, category: str, name: str, name_cn: str,
                 desc: str, func: Callable, engine: str = "brd",
                 needs: Optional[List[str]] = None,
                 outputs: Optional[List[str]] = None):
        if category not in CATEGORY_LABELS:
            raise ValueError(f"未知类别: {category}")
        self.id = id
        self.category = category
        self.name = name
        self.name_cn = name_cn
        self.desc = desc
        self.engine = engine
        self.needs = list(needs or [])
        self.outputs = list(outputs or [])
        self.func = func

    @property
    def engine_label(self) -> str:
        return ENGINE_LABELS.get(self.engine, self.engine)

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<Capability {self.id} {self.name_cn}>"


class Result:
    """一项能力的运行结果。

    `items` 是明细表, `columns` 描述它的列 (writer 按此渲染 Excel/CSV/HTML)。
    """

    __slots__ = ("id", "category", "name", "name_cn", "engine", "status",
                 "summary", "metrics", "columns", "items", "missing", "howto",
                 "sources", "warnings", "artifacts", "extra")

    def __init__(self, cap: Capability, status: str = INFO, summary: str = ""):
        self.id = cap.id
        self.category = cap.category
        self.name = cap.name
        self.name_cn = cap.name_cn
        self.engine = cap.engine
        self.status = status
        self.summary = summary
        self.metrics: List[dict] = []      # [{label, value, unit, source, confidence}]
        self.columns: List[str] = []       # items 的列名
        self.items: List[dict] = []        # 明细行 (key 必须落在 columns 内)
        self.missing: List[str] = []       # 缺什么数据
        self.howto: List[str] = []         # 怎么补 (Allegro 菜单路径 / 命令)
        self.sources: List[str] = []       # 数据出处汇总
        self.warnings: List[str] = []
        self.artifacts: List[dict] = []    # [{kind, path, note}] 生成的产物文件
        self.extra: Dict[str, Any] = {}    # 结构化中间结果, 供其它能力/报告复用

    # ---------------- 构造辅助 ----------------
    def metric(self, label: str, value: Any, unit: str = "",
               source: str = "", confidence: str = "") -> "Result":
        self.metrics.append({"label": label, "value": value, "unit": unit,
                             "source": source, "confidence": confidence})
        return self

    def add(self, **row) -> "Result":
        """追加一条明细; 未声明过的列自动登记 (保持出现顺序)。"""
        for key in row:
            if key not in self.columns:
                self.columns.append(key)
        self.items.append(row)
        return self

    def add_all(self, rows, columns: Optional[List[str]] = None) -> "Result":
        if columns:
            for col in columns:
                if col not in self.columns:
                    self.columns.append(col)
        for row in rows or []:
            self.add(**row)
        return self

    def need(self, what: str, howto: str = "") -> "Result":
        self.missing.append(what)
        if howto:
            self.howto.append(howto)
        return self

    def source(self, text: str) -> "Result":
        if text and text not in self.sources:
            self.sources.append(text)
        return self

    def warn(self, text: str) -> "Result":
        if text and text not in self.warnings:
            self.warnings.append(text)
        return self

    def artifact(self, kind: str, path: str, note: str = "") -> "Result":
        self.artifacts.append({"kind": kind, "path": path, "note": note})
        return self

    def degrade(self, status: str) -> "Result":
        """只允许把状态往"更保守"的方向调, 避免下游把 unavailable 洗成 pass。"""
        rank = {UNAVAILABLE: 0, SKIPPED: 0, INFO: 1, PASS: 2, WARN: 3, FAIL: 4}
        if rank.get(status, 0) < rank.get(self.status, 0):
            self.status = status
        return self

    # ---------------- 序列化 ----------------
    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)

    @property
    def measured(self) -> bool:
        """是否给出了真正的判定 (pass/fail/warn 都算, info 只算统计)。"""
        return self.status in (PASS, FAIL, WARN)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "category": self.category,
            "name": self.name, "name_cn": self.name_cn, "engine": self.engine,
            "status": self.status, "status_label": self.status_label,
            "summary": plain(self.summary),
            "metrics": plain(list(self.metrics)),
            "columns": list(self.columns),
            "items": plain(list(self.items)),
            "missing": plain(list(self.missing)),
            "howto": plain(list(self.howto)),
            "sources": plain(list(self.sources)),
            "warnings": plain(list(self.warnings)),
            "artifacts": plain(list(self.artifacts)),
            "extra": self.extra,
        }


def unavailable(cap: Capability, reason: str, howto: str = "",
                detail: Optional[dict] = None) -> Result:
    """数据不足的标准写法: 状态 unavailable + 明确缺什么 / 怎么补。"""
    res = Result(cap, UNAVAILABLE, reason)
    res.need(reason, howto)
    for key, value in (detail or {}).items():
        res.extra[key] = value
    return res


# 报告是纯文本/Excel/HTML, 不渲染 Markdown。各能力写结论时爱用 **强调**, 这里
# 统一抹掉标记, 免得导出后满屏星号。
_MD_RE = re.compile(r"\*\*|`{1,2}")


def plain(value):
    """去掉 Markdown 标记; 非字符串原样返回 (递归处理列表/字典)。"""
    if isinstance(value, str):
        return _MD_RE.sub("", value)
    if isinstance(value, list):
        return [plain(v) for v in value]
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    return value
