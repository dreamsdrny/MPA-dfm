"""store-brd / store-bom 资料库联动检查: BRD 与 BOM 自动配对 + 双向核对。

目录约定 (与 exe / 项目根同目录)::

    store-brd/   Allegro .brd; 同目录还可放 place_txt.txt / evan_components.csv
                 给没有 objects/Part.xml 的 FAB 图补位号清单
    store-bom/   BOM 表 (.xlsx / .csv), 表头由 src/design_inputs/bom.py 归一化

配对规则 (强 -> 弱):
    1. BOM 里某一行的料号 = BRD 文件名料号 (例如 BOM 的 PCB1 行 IPN = S-860-000000)
    2. 两个文件名共享 >=5 位的数字串 (S-860-000000 与 S-850-000000 共享 000739)
    3. 都没有: 不猜, 列为"未配对", 由人处理

核对项: 料号 / 版本 / BOM 位号质量 / BRD 位号来源 / 位号双向核对 / 非电气项 /
BRD 设计量。判定只用能被数据证明的结论, 缺数据一律写"数据不足", 不拿文件名硬判。
"""
import os
import re
import sys
from datetime import datetime

from .bom import read_bom
from .reconcile import brd_component_records, reconcile_bom

BRD_DIR = "store-brd"
BOM_DIR = "store-bom"
BRD_EXTS = (".brd",)
BOM_EXTS = (".xlsx", ".xlsm", ".csv")

STATUS_LABEL = {"pass": "通过", "warn": "需确认", "fail": "不符合",
                "unavailable": "数据不足", "info": "信息"}

# 这些前缀不是元器件 (拼板图/原理图/装配图/钢网/锡膏/基准点/安装孔...), 不参与位号双向核对
NON_ELECTRICAL_PREFIX = {
    "PCB", "SCH", "ASSY", "ASSYDWG", "SOLDER", "PASTE", "STENCIL", "PALLET",
    "FIXTURE", "LABEL", "DOC", "DRAWING", "DWG", "PACK",
    "FID", "FIDUCIAL", "MH", "MTG", "HOLE",
}

_PART_RE = re.compile(r"([A-Z]{1,3})-(\d{3})-(\d{6})(?:-([A-Z0-9]{1,4}))?", re.IGNORECASE)
_CORE_RE = re.compile(r"\b(\d{3})-(\d{6})\b")
_REV_RE = re.compile(r"rev[\s_.\-]*([A-Z0-9]{1,4})(?![A-Z0-9])", re.IGNORECASE)
_DIGIT_RUN_RE = re.compile(r"\d{5,}")
_REF_WITH_NUM_RE = re.compile(r"^[A-Za-z]+\d+$")
_PREFIX_RE = re.compile(r"^([A-Za-z]+)")


# ---------------------------------------------------------------- 目录与清单
def app_base_dir() -> str:
    """程序根目录: 源码运行时为项目根, 打包运行时为 exe 所在目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def store_paths(root=None, brd_dir=None, bom_dir=None) -> dict:
    """解析三个目录; 显式传入的路径优先, 否则用 <root>/store-brd 与 /store-bom。"""
    base = os.path.abspath(root) if root else app_base_dir()
    return {
        "root": base,
        "brd": os.path.abspath(brd_dir) if brd_dir else os.path.join(base, BRD_DIR),
        "bom": os.path.abspath(bom_dir) if bom_dir else os.path.join(base, BOM_DIR),
        "out": os.path.join(base, "output"),
    }


def _file_entry(path: str) -> dict:
    stat = os.stat(path)
    return {
        "path": os.path.abspath(path),
        "name": os.path.basename(path),
        "size": stat.st_size,
        "size_mb": round(stat.st_size / 1048576.0, 2),
        "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
    }


def scan_store(folder: str, exts) -> list:
    """列出目录下指定扩展名的文件 (按文件名排序); 目录不存在返回空列表。"""
    if not folder or not os.path.isdir(folder):
        return []
    found = []
    for name in sorted(os.listdir(folder)):
        if not name.lower().endswith(tuple(exts)):
            continue
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            continue
        try:
            found.append(_file_entry(path))
        except OSError:
            continue
    return found


def scan_brd(folder: str) -> list:
    return scan_store(folder, BRD_EXTS)


def scan_bom(folder: str) -> list:
    return scan_store(folder, BOM_EXTS)


# ---------------------------------------------------------------- 料号与配对
def name_info(name: str) -> dict:
    """从文件名提取料号 / 数字核 / 版本: S-860-000000_FAB_BRD_Rev_E -> 860-000739 / E。"""
    stem = os.path.splitext(os.path.basename(str(name)))[0]
    part, core = "", ""
    matched = _PART_RE.search(stem)
    if matched:
        part = f"{matched.group(1)}-{matched.group(2)}-{matched.group(3)}".upper()
        core = f"{matched.group(2)}-{matched.group(3)}"
    else:
        fallback = _CORE_RE.search(stem)
        if fallback:
            core = f"{fallback.group(1)}-{fallback.group(2)}"
    rev = _REV_RE.search(stem)
    return {
        "part_number": part,
        "core": core,
        "rev": rev.group(1).upper() if rev else "",
        "digits": _DIGIT_RUN_RE.findall(stem),
    }


def _bom_part_numbers(bom_info: dict) -> set:
    numbers = {str(value).strip().upper()
               for value in (bom_info or {}).get("part_numbers") or [] if str(value).strip()}
    for row in (bom_info or {}).get("rows") or []:
        number = str(row.get("part_number") or "").strip().upper()
        if number:
            numbers.add(number)
    return numbers


def _shared_core(brd_id: dict, bom_id: dict, bom_parts: set) -> str:
    """BRD 与 BOM 共用的 >=5 位数字串 (料号尾段), 用于跨料号前缀配对。"""
    wanted = set(brd_id.get("digits") or [])
    if bom_id.get("digits"):
        shared = wanted & set(bom_id["digits"])
        if shared:
            return max(shared, key=len)
    for number in bom_parts:
        shared = wanted & set(_DIGIT_RUN_RE.findall(number))
        if shared:
            return max(shared, key=len)
    return ""


def pair_stores(brd_files, bom_files, bom_infos) -> dict:
    """按料号最强证据一对一配对; 返回 pairs / unmatched_brd / unmatched_bom。"""
    candidates = []
    for brd in brd_files:
        brd_id = name_info(brd["name"])
        for bom in bom_files:
            bom_id = name_info(bom["name"])
            parts = _bom_part_numbers(bom_infos.get(bom["path"]) or {})
            score, reasons = 0, []
            if brd_id["part_number"] and brd_id["part_number"] in parts:
                score = 100
                reasons.append(f"BOM 料号含 {brd_id['part_number']}")
            else:
                shared = _shared_core(brd_id, bom_id, parts)
                if shared:
                    score = 60
                    reasons.append(f"料号共用数字 {shared}")
            if not score:
                continue
            if brd_id["rev"] and bom_id["rev"] and brd_id["rev"] == bom_id["rev"]:
                score += 5
                reasons.append("文件名版本相同")
            candidates.append((score, brd, bom, reasons))
    candidates.sort(key=lambda item: -item[0])

    used_brd, used_bom, pairs = set(), set(), []
    for score, brd, bom, reasons in candidates:
        if brd["path"] in used_brd or bom["path"] in used_bom:
            continue
        used_brd.add(brd["path"])
        used_bom.add(bom["path"])
        pairs.append({"brd": brd, "bom": bom, "score": score, "reasons": reasons})

    unmatched_brd = []
    for brd in brd_files:
        if brd["path"] in used_brd:
            continue
        brd_id = name_info(brd["name"])
        want = brd_id["part_number"] or brd_id["core"] or "（文件名无料号）"
        unmatched_brd.append({**brd, "part_number": brd_id["part_number"],
                              "reason": f"store-bom 里没有料号对应 {want} 的 BOM"})
    unmatched_bom = []
    for bom in bom_files:
        if bom["path"] in used_bom:
            continue
        parts = sorted(_bom_part_numbers(bom_infos.get(bom["path"]) or {}))[:6]
        unmatched_bom.append({**bom,
                              "reason": "store-brd 里没有与该 BOM 料号对应的 .brd"
                                        + (f"（BOM 料号: {'、'.join(parts)}）" if parts else "")})
    return {"pairs": pairs, "unmatched_brd": unmatched_brd, "unmatched_bom": unmatched_bom}


# ---------------------------------------------------------------- 资料读取
def is_electrical(refdes: str) -> bool:
    """按位号判断是不是元器件: PCB1/SCH1/ASSYDWG1/SOLDER_PASTE2 这类不算。"""
    text = str(refdes or "").strip()
    if not _REF_WITH_NUM_RE.match(text):
        return False
    matched = _PREFIX_RE.match(text)
    return bool(matched) and matched.group(1).upper() not in NON_ELECTRICAL_PREFIX


def split_bom_rows(rows, known_refdes=()) -> tuple:
    """把 BOM 行分成 (元器件行, 非电气项) —— 非电气项列出但不参与位号核对。

    传了 ``known_refdes`` (BRD 的位号清单) 时, 只要该位号在 BRD 里存在, 就一定按
    元器件参与核对: 有些板子把 MH1 / FID1 这类位号当作器件放在 Part.xml 里,
    不能仅凭前缀把它们排除, 否则会刷出假的 "BOM 漏项"。
    """
    known = {str(refdes or "").strip().upper() for refdes in known_refdes or ()}
    electrical, others = [], []
    for row in rows or []:
        refdes = str(row.get("refdes") or "").strip()
        if is_electrical(refdes) or (known and refdes.upper() in known):
            electrical.append(row)
        else:
            others.append(row)
    return electrical, others


def brd_records(brd_path: str) -> dict:
    """BRD 侧位号清单: 优先 objects/Part.xml, 退回同目录的 Placement/SKILL 导出。"""
    part = brd_component_records(brd_path)
    warnings = list(part.get("warnings") or [])
    if part.get("ok") and part.get("records"):
        return {
            "ok": True, "source": "objects/Part.xml", "confidence": "high",
            "records": [{"refdes": item.get("refdes"),
                         "package": item.get("package") or "",
                         "category": item.get("category") or "",
                         "source": item.get("source") or "objects/Part.xml"}
                        for item in part["records"]],
            "warnings": warnings,
        }

    # FAB 拼板图常常没有 Part.xml: 同目录的 place_txt.txt / evan_components.csv 可以补位号
    try:
        from src.component_stats import load_component_inventory
        inventory = load_component_inventory(brd_path)
    except Exception as exc:  # noqa: BLE001  导出解析失败不影响主流程, 只记原因
        inventory = {"ok": False, "warnings": [f"读取导出文件失败: {exc}"]}
    warnings.extend(inventory.get("warnings") or [])
    if inventory.get("ok") and inventory.get("components"):
        # inventory 自己也会读 Part.xml; 只有它确实没有 Part.xml 时才算"导出补位号"
        from_partxml = bool(inventory.get("has_partxml"))
        return {
            "ok": True,
            "source": "objects/Part.xml" if from_partxml
                      else "Allegro Placement / SKILL 导出",
            "confidence": "high" if from_partxml else "medium",
            "records": [{"refdes": item.get("refdes"),
                         "package": item.get("package") or "",
                         "category": item.get("category") or "",
                         "source": "、".join(item.get("sources") or [])
                                   or ("objects/Part.xml" if from_partxml else "导出文件")}
                        for item in inventory["components"]],
            "warnings": warnings,
        }
    return {
        "ok": False, "source": "", "confidence": "none", "records": [],
        "warnings": warnings,
        "reason": (part.get("warnings") or [None])[0]
                  or "BRD 里没有 objects/Part.xml, 同目录也没有 place_txt.txt / evan_components.csv",
    }


def _design_text(design: dict) -> str:
    bits = []
    name = design.get("board_name") or design.get("design_name")
    if name:
        bits.append(str(name))
    if design.get("layer_count"):
        bits.append(f"{design['layer_count']} 层")
    if design.get("net_count"):
        bits.append(f"{design['net_count']} 网络")
    if design.get("part_count"):
        bits.append(f"{design['part_count']} 器件")
    size = design.get("board_size_mm")
    if (isinstance(size, (list, tuple)) and len(size) == 2
            and all(isinstance(value, (int, float)) for value in size)):
        bits.append(f"板框 {float(size[0]):.2f}×{float(size[1]):.2f} mm")
    return " · ".join(bits) or "BRD 已读取"


def _design_metrics(design: dict) -> str:
    bits = []
    for key, label, unit in (("min_line_width_mm", "最小线宽", "mm"),
                             ("min_spacing_mm", "最小间距", "mm"),
                             ("min_drill_mm", "最小钻孔", "mm"),
                             ("board_thickness_mm", "板厚", "mm")):
        value = design.get(key)
        if isinstance(value, (int, float)) and value > 0:
            bits.append(f"{label} {float(value):.3f} {unit}")
    return " · ".join(bits)


def _bom_summary(info: dict, known_refdes=()) -> dict:
    rows = info.get("rows") or []
    electrical, others = split_bom_rows(rows, known_refdes)
    stats = info.get("stats") or {}
    return {
        "rows": len(rows),
        "electrical": len(electrical),
        "non_electrical": len(others),
        "part_numbers": len(info.get("part_numbers") or []),
        "format": info.get("format"),
        "sheet": info.get("sheet") or "",
        "header_row": info.get("header_row"),
        "skipped_no_refdes": stats.get("skipped_no_refdes", 0),
        "duplicates": list(stats.get("duplicate_refdes") or []),
        "warnings": list(info.get("warnings") or []),
        "unmapped": list(info.get("unmapped") or []),
    }


# ---------------------------------------------------------------- 单对检查
def check_pair(brd_file, bom_file, bom_info=None, log=None) -> dict:
    """核对一对 BRD/BOM, 返回配对结论 + 逐项检查 + 位号核对明细。"""
    brd_path = brd_file["path"] if isinstance(brd_file, dict) else str(brd_file)
    bom_path = bom_file["path"] if isinstance(bom_file, dict) else str(bom_file)
    brd_entry = dict(brd_file) if isinstance(brd_file, dict) else _file_entry(brd_path)
    bom_entry = dict(bom_file) if isinstance(bom_file, dict) else _file_entry(bom_path)
    brd_id = name_info(brd_entry["name"])
    bom_id = name_info(bom_entry["name"])
    checks, warnings = [], []

    def add(check_id, name, status, detail):
        checks.append({"id": check_id, "name": name, "status": status,
                       "status_label": STATUS_LABEL.get(status, status),
                       "detail": detail})

    # ---- BRD 读取 ----
    design = {}
    try:
        from src.brd_design import extract_brd_design
        design = extract_brd_design(brd_path)
        if not design.get("ok"):
            add("brd.read", "BRD 读取", "fail",
                design.get("error") or "BRD 解析失败")
            design = {}
        else:
            add("brd.read", "BRD 读取", "pass", _design_text(design))
    except (OSError, ValueError) as exc:
        add("brd.read", "BRD 读取", "fail", str(exc))

    # 先取 BRD 位号, 供 BOM 的"元器件/非电气项"分类使用 (MH1 这类位号不能只看前缀)
    try:
        records = brd_records(brd_path)
    except Exception as exc:  # noqa: BLE001  单个文件的异常不能中断整库检查
        records = {"ok": False, "records": [], "source": "",
                   "reason": f"位号清单读取失败: {exc}"}
    known_refs = [item.get("refdes") for item in records["records"]] if records["ok"] else ()

    # ---- BOM 读取 ----
    if bom_info is None:
        try:
            bom_info = read_bom(bom_path, strict=False)
        except (OSError, ValueError) as exc:
            bom_info = None
            add("bom.read", "BOM 读取", "fail", str(exc))
    if bom_info:
        summary = _bom_summary(bom_info, known_refs)
        add("bom.read", "BOM 读取", "pass",
            f"{summary['rows']} 个位号 (元器件 {summary['electrical']} / "
            f"非电气 {summary['non_electrical']}) · 表头第 {summary['header_row']} 行")
        if summary["warnings"]:
            add("bom.quality", "BOM 数据质量", "warn",
                "；".join(summary["warnings"][:4]))
        else:
            detail = "位号无重复, 数量与位号数一致"
            if summary["duplicates"]:
                detail = "位号重复 " + str(len(summary["duplicates"])) + " 个"
            if summary["skipped_no_refdes"]:
                detail += (f"; 跳过 {summary['skipped_no_refdes']} 行没有位号的行"
                           "(表头/分隔/合计行属正常)")
            add("bom.quality", "BOM 数据质量", "pass", detail)
    else:
        summary = {}

    # ---- 料号一致 ----
    parts = _bom_part_numbers(bom_info or {})
    board_rows = []
    if brd_id["part_number"]:
        board_rows = [row for row in (bom_info or {}).get("rows") or []
                      if str(row.get("part_number") or "").strip().upper()
                      == brd_id["part_number"]]
    if not brd_id["part_number"]:
        add("id.part", "料号一致", "unavailable",
            "BRD 文件名里看不出料号 (形如 S-860-000000), 无法与 BOM 对上")
    elif brd_id["part_number"] in parts:
        matched_rows = board_rows[:1]
        where = ""
        if matched_rows:
            ref = matched_rows[0].get("refdes") or "（无位号）"
            where = f"：BOM 里 {ref} 行的料号就是它"
        add("id.part", "料号一致", "pass", brd_id["part_number"] + where)
    else:
        add("id.part", "料号一致", "warn",
            f"BRD 料号 {brd_id['part_number']} 不在 BOM 的料号列里; "
            f"BOM 里的料号: {'、'.join(sorted(parts)[:6]) or '（空）'}")

    # ---- 版本一致 ----
    if brd_id["rev"] and bom_id["rev"]:
        if brd_id["rev"] == bom_id["rev"]:
            add("id.rev", "版本一致", "pass", f"两侧文件名版本都是 {brd_id['rev']}")
        else:
            add("id.rev", "版本一致", "warn",
                f"BRD 版本 {brd_id['rev']} / BOM 版本 {bom_id['rev']}; "
                "FAB 图与装配 BOM 的版本号口径可能不同, 请确认是同一版")
    else:
        add("id.rev", "版本一致", "unavailable", "至少一侧文件名里没有版本号")

    # ---- BRD 位号来源 ----
    if records["ok"]:
        add("brd.refdes", "BRD 位号清单", "pass",
            f"{len(records['records'])} 个位号, 来源: {records['source']}")
    else:
        add("brd.refdes", "BRD 位号清单", "unavailable",
            (records.get("reason") or "没有位号清单")
            + "；可在 store-brd 同目录放 place_txt.txt / evan_components.csv, "
              "或改用带 objects/Part.xml 的设计 .brd "
              "(坐标导出脚本可用 --export-skill 生成)")

    # ---- 位号双向核对 ----
    electrical, others = split_bom_rows((bom_info or {}).get("rows") or [], known_refs)
    reconciliation = None
    if electrical:
        # BRD 没有位号清单时也生成明细: 每行状态是 unverified, 但 BOM 内容列得出来
        reconciliation = reconcile_bom(records["records"] if records["ok"] else [],
                                       electrical)
    if not records["ok"]:
        add("refdes.reconcile", "位号双向核对", "unavailable",
            "BRD 没有位号清单, BOM 位号无法逐项核对 (BOM 内容仍逐行列在明细里, 状态为未核对)")
    elif not electrical:
        add("refdes.reconcile", "位号双向核对", "unavailable",
            "BOM 里没有可核对的元器件位号")
    else:
        counts = reconciliation["summary"]
        detail = (f"BRD {counts['brd_count']} / BOM {counts['bom_count']} / "
                  f"匹配 {counts['matched_count']} / BOM 漏项 {counts['missing_in_bom_count']} / "
                  f"BOM 多出 {counts['missing_in_brd_count']} / 属性冲突 {counts['conflict_count']}")
        status = {"verified": "pass", "incomplete": "warn",
                  "review": "warn", "unavailable": "unavailable"}.get(
            reconciliation["status"], "warn")
        add("refdes.reconcile", "位号双向核对", status, detail)
        warnings.extend(reconciliation.get("warnings") or [])

    # ---- 非电气项 ----
    if others:
        sample = "、".join(str(row.get("refdes")) for row in others[:6])
        add("bom.non_electrical", "非电气项", "info",
            f"{len(others)} 项已排除在位号核对之外: {sample}"
            + ("…" if len(others) > 6 else ""))
    else:
        add("bom.non_electrical", "非电气项", "info", "没有非电气项")

    # ---- BRD 设计量 ----
    metrics = _design_metrics(design)
    if metrics:
        add("brd.metrics", "BRD 设计量", "info", metrics)

    statuses = {item["status"] for item in checks}
    if "fail" in statuses:
        verdict = "fail"
    elif statuses & {"warn", "unavailable"}:
        verdict = "warn"
    else:
        verdict = "pass"

    return {
        "brd": {**brd_entry, **brd_id},
        "bom": {**bom_entry, **bom_id, "summary": summary},
        "verdict": verdict,
        "verdict_label": STATUS_LABEL.get(verdict, verdict),
        "checks": checks,
        "warnings": list(dict.fromkeys(str(item) for item in warnings if item)),
        "brd_records": {
            "ok": records["ok"], "source": records["source"],
            "count": len(records["records"]), "confidence": records.get("confidence"),
            "reason": records.get("reason") or "",
        },
        "bom_items": {
            "electrical": len(electrical),
            "non_electrical": [{"refdes": row.get("refdes"),
                                "part_number": row.get("part_number"),
                                "value": row.get("value"),
                                "description": row.get("description")}
                               for row in others],
        },
        "reconciliation": reconciliation,
    }


# ---------------------------------------------------------------- 全库检查
def run_check(root=None, brd_dir=None, bom_dir=None, log=None) -> dict:
    """扫描 store-brd / store-bom, 配对并逐对检查, 返回可 JSON 序列化的结论。"""
    def emit(text, tag=None):
        if callable(log):
            log(text, tag)

    dirs = store_paths(root=root, brd_dir=brd_dir, bom_dir=bom_dir)
    warnings = []
    emit(f"[资料库] BRD 目录: {dirs['brd']}")
    emit(f"[资料库] BOM 目录: {dirs['bom']}")
    brd_files = scan_brd(dirs["brd"])
    bom_files = scan_bom(dirs["bom"])
    for key, folder, files in (("brd", dirs["brd"], brd_files), ("bom", dirs["bom"], bom_files)):
        if not os.path.isdir(folder):
            warnings.append(f"目录不存在: {folder}")
        elif not files:
            warnings.append(f"目录里没有可用文件: {folder}")
    emit(f"[扫描] {len(brd_files)} 个 BRD / {len(bom_files)} 个 BOM")
    legacy = [name for name in (os.listdir(dirs["bom"]) if os.path.isdir(dirs["bom"]) else [])
              if name.lower().endswith(".xls")]
    if legacy:
        warnings.append(f"BOM 目录里有 {len(legacy)} 个 .xls (旧版 Excel), 请另存为 .xlsx: "
                        + "、".join(legacy[:3]))
        emit(f"[BOM] 旧版 .xls 未读取: {'、'.join(legacy[:3])}", "warn")

    bom_infos, bom_entries = {}, []
    for entry in bom_files:
        try:
            info = read_bom(entry["path"], strict=False)
            bom_infos[entry["path"]] = info
            bom_entries.append({**entry, "status": "parsed",
                                "summary": _bom_summary(info)})
            emit(f"[BOM] {entry['name']}: {len(info['rows'])} 个位号", "success")
        except (OSError, ValueError) as exc:
            bom_entries.append({**entry, "status": "error", "summary": {},
                                "error": str(exc)})
            warnings.append(f"{entry['name']}: {exc}")
            emit(f"[BOM] {entry['name']}: {exc}", "error")

    pairing = pair_stores(brd_files, bom_files, bom_infos)
    pairs = []
    for item in pairing["pairs"]:
        try:
            pair = check_pair(item["brd"], item["bom"],
                              bom_info=bom_infos.get(item["bom"]["path"]),
                              log=log)
        except Exception as exc:  # noqa: BLE001  一对检查失败不能中断整库检查
            detail = f"检查这一对时出错: {exc}"
            warnings.append(f"{item['brd']['name']} ↔ {item['bom']['name']}: {exc}")
            pair = {
                "brd": item["brd"], "bom": item["bom"],
                "verdict": "fail", "verdict_label": STATUS_LABEL["fail"],
                "checks": [{"id": "internal", "name": "检查执行", "status": "fail",
                            "status_label": STATUS_LABEL["fail"], "detail": detail}],
                "warnings": [str(exc)],
                "brd_records": {"ok": False, "source": "", "count": 0, "reason": detail},
                "bom_items": {"electrical": 0, "non_electrical": []},
                "reconciliation": None,
            }
        pair["score"] = item["score"]
        pair["match_reasons"] = item["reasons"]
        pairs.append(pair)
        emit(f"[配对] {pair['brd']['name']} ↔ {pair['bom']['name']}: "
             f"{pair['verdict_label']}",
             "success" if pair["verdict"] == "pass" else "warn")
        for check in pair["checks"]:
            if check["status"] in ("warn", "fail"):
                emit(f"    [{check['status_label']}] {check['name']}: {check['detail']}", "warn")

    for item in pairing["unmatched_brd"]:
        emit(f"[未配对] {item['name']}: {item['reason']}", "warn")
    for item in pairing["unmatched_bom"]:
        emit(f"[未配对] {item['name']}: {item['reason']}", "warn")

    stats = {
        "brd_count": len(brd_files),
        "bom_count": len(bom_files),
        "pair_count": len(pairs),
        "pass": sum(1 for pair in pairs if pair["verdict"] == "pass"),
        "warn": sum(1 for pair in pairs if pair["verdict"] == "warn"),
        "fail": sum(1 for pair in pairs if pair["verdict"] == "fail"),
        "unmatched_brd": len(pairing["unmatched_brd"]),
        "unmatched_bom": len(pairing["unmatched_bom"]),
        "brd_refdes": sum(pair["brd_records"]["count"] for pair in pairs),
        "bom_refdes": sum((pair["bom"].get("summary") or {}).get("rows", 0) for pair in pairs),
    }
    if not brd_files or not bom_files:
        verdict = "unavailable"
    elif stats["fail"]:
        verdict = "fail"
    elif stats["warn"] or stats["unmatched_brd"] or stats["unmatched_bom"]:
        verdict = "warn"
    else:
        verdict = "pass"

    return {
        "ok": bool(brd_files and bom_files),
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "dirs": dirs,
        "brd_files": brd_files,
        "bom_files": bom_entries,
        "pairs": pairs,
        "unmatched_brd": pairing["unmatched_brd"],
        "unmatched_bom": pairing["unmatched_bom"],
        "stats": stats,
        "verdict": verdict,
        "verdict_label": STATUS_LABEL.get(verdict, verdict),
        "warnings": warnings,
    }


def summary_lines(bundle: dict) -> list:
    """给 CLI / 界面用的几行结论文本。"""
    stats = bundle.get("stats") or {}
    lines = [
        (f"资料库: {bundle['dirs']['brd']} ({stats.get('brd_count', 0)} 个 BRD) / "
         f"{bundle['dirs']['bom']} ({stats.get('bom_count', 0)} 个 BOM)"),
        (f"配对: {stats.get('pair_count', 0)} 对, 结论 {bundle.get('verdict_label')} "
         f"(通过 {stats.get('pass', 0)} / 需确认 {stats.get('warn', 0)} / "
         f"不符合 {stats.get('fail', 0)})"),
    ]
    if stats.get("unmatched_brd") or stats.get("unmatched_bom"):
        lines.append(f"未配对: BRD {stats.get('unmatched_brd', 0)} 个 / "
                     f"BOM {stats.get('unmatched_bom', 0)} 个")
    return lines
