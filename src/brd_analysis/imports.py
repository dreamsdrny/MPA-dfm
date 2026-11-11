"""Allegro 导出文件导入: DRC 报告 / Placement / Excellon / 网表 / IPC-356 / 通用报告。

为什么要这一层
------------
`.brd` 里没有的东西 (每个孔的坐标、器件 XY、DRC 违规明细、网表) 只有一个来源:
PCB Editor 自己导出。用户手上往往已经有这些文件 (板厂回传的 DRC 报告、SMT 的
坐标文件、测试厂的 IPC-356), 把它们读进来, 分析能力才完整。

本模块的纪律:
  * 解析失败就说失败, 并写明"这个文件的格式没认出来, 请把前几行发回来",
    不返回空表让上层以为"这板子没有违规";
  * 每个解析结果都带 `source`(哪个文件) 与 `raw_kind`, 报告里能说明数据出处;
  * Allegro 的报告是**定宽列 + 横线分隔**的文本, 用同一套通用解析器处理,
    不针对某一种报告硬编码列位置。
"""
import os
import re
from typing import Dict, Iterable, List, Optional

# --------------------------------------------------------------------------
# 文本读取: Allegro 在中文 Windows 上常导 GBK, 先按 utf-8 试, 再退 gbk/latin-1
# --------------------------------------------------------------------------
def read_text(path: str) -> str:
    with open(path, "rb") as f:
        raw = f.read()
    for enc in ("utf-8-sig", "utf-8", "gbk", "big5", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", "replace")


def _basename(path: str) -> str:
    return os.path.basename(path)


# --------------------------------------------------------------------------
# 通用: Allegro 定宽报告解析
# --------------------------------------------------------------------------
_RULE_RE = re.compile(r"^\s*[-=_]{3,}\s*$")
_KV_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9 _\-/\.\(\)]{1,40}?)\s*[:=]\s*(.+?)\s*$")
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _split_columns(text: str) -> List[str]:
    return [c.strip() for c in re.split(r"\s{2,}", text.strip()) if c.strip()]


# 报告标题正常就是一行短文字。如果这里出现的是二进制片段 (典型场景: 有人把
# .brd 当报告丢进来), 取整行会让标题变成几十 KB 的乱码, 还会带着控制字符把
# Excel 写崩。所以标题只保留可打印部分并截断。
_PRINTABLE_RE = re.compile(r"[^\x20-\x7e\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]+")
_TITLE_MAX = 120


def _clean_title(line: str) -> str:
    text = _PRINTABLE_RE.sub(" ", line).strip()
    text = re.sub(r"\s{2,}", " ", text)
    if len(text) > _TITLE_MAX:
        text = text[:_TITLE_MAX] + "…"
    return text


def parse_allegro_report(path: str) -> dict:
    """通用报告解析: 抽出 (a) 标题, (b) key: value 行, (c) 定宽表格段。

    定宽表格的识别方式: 一行横线 (--- / === ) 上面那行是表头, 下面的行是数据,
    列按 2 个以上空格切分 —— Allegro 的报告就是这么排的。
    """
    text = read_text(path)
    lines = text.splitlines()
    title = ""
    for line in lines[:12]:
        if line.strip() and not _RULE_RE.match(line):
            title = _clean_title(line)
            break

    key_values: Dict[str, str] = {}
    sections: List[dict] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if _RULE_RE.match(line) and i > 0:
            header_line = lines[i - 1]
            if header_line.strip() and not _RULE_RE.match(header_line):
                columns = _split_columns(header_line)
                if len(columns) >= 2:
                    rows = []
                    j = i + 1
                    while j < n and lines[j].strip() and not _RULE_RE.match(lines[j]):
                        cells = _split_columns(lines[j])
                        if len(cells) == 1 and len(columns) > 1:
                            # 单列行通常是文字说明, 按第一列收
                            cells = cells + [""] * (len(columns) - 1)
                        if len(cells) > len(columns):
                            cells = cells[:len(columns) - 1] + [" ".join(cells[len(columns) - 1:])]
                        while len(cells) < len(columns):
                            cells.append("")
                        rows.append(dict(zip(columns, cells)))
                        j += 1
                    if rows:
                        sections.append({"header": " ".join(columns), "columns": columns,
                                         "rows": rows, "start_line": i})
                        i = j
                        continue
        m = _KV_RE.match(line)
        if m and len(m.group(1)) > 1:
            key_values.setdefault(m.group(1).strip(), m.group(2).strip())
        i += 1

    return {
        "ok": bool(title or sections or key_values),
        "kind": "report",
        "title": title,
        "key_values": key_values,
        "sections": sections,
        "source": _basename(path),
        "path": os.path.abspath(path),
    }


# --------------------------------------------------------------------------
# DRC 报告
# --------------------------------------------------------------------------
_DRC_HINT_RE = re.compile(
    r"(DRC|DESIGN RULE|VIOLATION|SPACING|CLEARANCE|CONSTRAINT)", re.I)
_DRC_CLASS_RE = re.compile(
    r"^\s*(?:ERROR\s+CLASS|CLASS|CONSTRAINT|RULE|TYPE|ERROR\s+NAME)\s*[:=]\s*(.+?)\s*$",
    re.I)
_DRC_LAYER_RE = re.compile(r"^\s*(?:LAYER|SUBCLASS|CLASS/SUBCLASS)\s*[:=]\s*(.+?)\s*$", re.I)
_DRC_ACTUAL_RE = re.compile(r"^\s*(?:ACTUAL|MEASURED|VALUE)\s*[:=]\s*(-?[\d.]+)", re.I)
_DRC_REQUIRED_RE = re.compile(r"^\s*(?:REQUIRED|CONSTRAINT\s+VALUE|EXPECTED)\s*[:=]\s*(-?[\d.]+)", re.I)
_DRC_LOC_RE = re.compile(r"^\s*(?:LOCATION|COORDINATES?|XY|POSITION)\s*[:=]\s*(.+?)\s*$", re.I)
_DRC_NET_RE = re.compile(r"^\s*(?:NET|NETS|NET\s+NAME)\s*[:=]\s*(.+?)\s*$", re.I)
_DRC_REFDES_RE = re.compile(r"^\s*(?:REFDES|SYMBOL|COMPONENT|REF)\s*[:=]\s*(.+?)\s*$", re.I)


def _looks_like_drc(text: str) -> bool:
    head = "\n".join(text.splitlines()[:60])
    return bool(_DRC_HINT_RE.search(head))


def parse_drc_report(path: str) -> dict:
    """解析 Allegro DRC 报告。

    Allegro 各版本的 DRC 报告排版不完全一样, 所以这里做两件事:
      1. 按空行分块, 块内按 `键 : 值` 抽字段 (Class / Layer / Actual / Required / Location);
      2. 抽不到的块也保留原文摘要, 不让违规"消失"。
    返回 items 里每条 = 一条违规; 另外给出按 Class 的汇总。
    """
    text = read_text(path)
    blocks: List[List[str]] = []
    cur: List[str] = []
    for line in text.splitlines():
        if line.strip():
            cur.append(line.rstrip())
        elif cur:
            blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)

    items: List[dict] = []
    unrecognized = 0
    for block in blocks:
        joined = "\n".join(block)
        # 一个块只要出现 "ERROR CLASS: xxx" 之类的字段, 就一定是 DRC 条目 ——
        # 不能只靠 SPACING/SHORT 这些关键字判断, 否则 "Unconnected Pin"、
        # "Short Circuit" 这类条目会被整块丢掉 (实测踩过)。
        has_class_field = any(_DRC_CLASS_RE.match(line) for line in block)
        if not has_class_field and not _DRC_HINT_RE.search(joined):
            continue
        cls = layer = loc = net = ref = None
        actual = required = None
        for line in block:
            m = _DRC_CLASS_RE.match(line)
            if m and not cls:
                cls = m.group(1).strip()
            m = _DRC_LAYER_RE.match(line)
            if m and not layer:
                layer = m.group(1).strip()
            m = _DRC_ACTUAL_RE.match(line)
            if m and actual is None:
                actual = float(m.group(1))
            m = _DRC_REQUIRED_RE.match(line)
            if m and required is None:
                required = float(m.group(1))
            m = _DRC_LOC_RE.match(line)
            if m and not loc:
                loc = m.group(1).strip()
            m = _DRC_NET_RE.match(line)
            if m and not net:
                net = m.group(1).strip()
            m = _DRC_REFDES_RE.match(line)
            if m and not ref:
                ref = m.group(1).strip()
        if cls is None and actual is None:
            unrecognized += 1
            continue
        items.append({
            "class": cls or "(未标注类别)",
            "layer": layer or "",
            "actual": actual,
            "required": required,
            "location": loc or "",
            "net": net or "",
            "refdes": ref or "",
            "detail": " / ".join(x.strip() for x in block[:3] if x.strip())[:200],
        })

    classes: Dict[str, int] = {}
    for it in items:
        classes[it["class"]] = classes.get(it["class"], 0) + 1

    ok = bool(items)
    return {
        "ok": ok,
        "kind": "drc",
        "source": _basename(path),
        "path": os.path.abspath(path),
        "items": items,
        "count": len(items),
        "by_class": dict(sorted(classes.items(), key=lambda kv: -kv[1])),
        "unrecognized_blocks": unrecognized,
        "reason": "" if ok else
                  ("这个文件里没有解析出 DRC 违规条目 (可能是汇总报告而不是明细, "
                   "或排版与本解析器不符); 请把文件前 30 行发回来以便适配"),
    }


# --------------------------------------------------------------------------
# 网表 (Allegro 第三方网表 / 简易 "net: pin" 文本)
# --------------------------------------------------------------------------
_NET_KW_RE = re.compile(r"^\s*'NET'\s+'([^']+)'")
_PIN_KW_RE = re.compile(r"^\s*'PIN'\s+'([^']+)'\s*'([^']*)'")


def _looks_like_netlist(text: str) -> bool:
    head = "\n".join(text.splitlines()[:200])
    if "'NET'" in head.upper().replace("'NET'", "'NET'"):
        return True
    return bool(re.search(r"^\s*NET\s*[:=]\s*\S", head, re.I | re.M))


def parse_netlist(path: str) -> dict:
    """解析网表: 支持 Allegro pstxnet.dat 的 'NET'/'PIN' 关键字格式, 也支持
    `net: pin1 pin2 ...` 这种简易文本。
    """
    text = read_text(path)
    nets: Dict[str, List[str]] = {}
    cur = None
    for line in text.splitlines():
        m = _NET_KW_RE.match(line)
        if m:
            cur = m.group(1).strip()
            nets.setdefault(cur, [])
            continue
        m = _PIN_KW_RE.match(line)
        if m and cur:
            pin = m.group(1).strip()
            ref = m.group(2).strip()
            nets[cur].append(f"{ref}.{pin}" if ref else pin)
            continue
        m = re.match(r"^\s*NET\s*[:=]\s*(\S+)\s*[:= ]\s*(.*)$", line, re.I)
        if m:
            cur = m.group(1).strip()
            pins = [p for p in re.split(r"[\s,]+", m.group(2)) if p]
            nets.setdefault(cur, []).extend(pins)
    ok = bool(nets)
    return {
        "ok": ok,
        "kind": "netlist",
        "source": _basename(path),
        "path": os.path.abspath(path),
        "nets": nets,
        "count": len(nets),
        "pin_count": sum(len(v) for v in nets.values()),
        "reason": "" if ok else "没有解析出网络连接 (需要 pstxnet.dat 或 'NET: pid1 pid2' 文本)",
    }


# --------------------------------------------------------------------------
# IPC-356 (IPC-D-356 网表测试文件)
# --------------------------------------------------------------------------
def parse_ipc356(path: str) -> dict:
    """解析 IPC-D-356 网表测试文件。

    记录类型: 317 = 网络名, 327 = 连通性测试点, 367 = 绝缘测试点。
    字段是定宽/自由格式混排, 这里只取可靠的三样: 记录类型 / 网络名 / 测试点坐标。
    """
    text = read_text(path)
    nets: Dict[str, int] = {}
    points: List[dict] = []
    cur_net = ""
    for line in text.splitlines():
        rec = line.rstrip()
        code = rec[:3]
        if code == "317":
            cur_net = rec[3:].strip().rstrip(".").strip()
            nets.setdefault(cur_net, 0)
        elif code in ("327", "367"):
            nets[cur_net] = nets.get(cur_net, 0) + 1
            refdes_m = re.search(r"\b([A-Z]{1,4}\d{1,5})\b", rec[3:])
            refdes = refdes_m.group(1) if refdes_m else ""
            # 数值扫描必须**跳过位号那一段**, 否则 "TP1 +00017780+00017780"
            # 会把位号里的 1 也扫进来, 变成 [1, 17780, 17780] (实测踩过)。
            tail = rec[3:]
            scan_from = refdes_m.end() if refdes_m else 0
            nums = []
            for token in _NUM_RE.findall(tail[scan_from:]):
                try:
                    nums.append(float(token))
                except ValueError:
                    continue
            points.append({
                "net": cur_net,
                "type": "continuity" if code == "327" else "isolation",
                "refdes": refdes,
                "tail": tail.strip()[:80],
                "nums": nums[:6],
            })
    ok = bool(points) or bool(nets)
    return {
        "ok": ok,
        "kind": "ipc356",
        "source": _basename(path),
        "path": os.path.abspath(path),
        "nets": nets,
        "count": len(nets),
        "points": points,
        "point_count": len(points),
        "reason": "" if ok else "没有识别出 317/327/367 记录, 可能不是 IPC-D-356 文件",
    }


# --------------------------------------------------------------------------
# 发现与入口
# --------------------------------------------------------------------------
_BRIDGE_DIRS = ("allegro_bridge", "output", "exports")


def discover(paths: Iterable[str]) -> List[dict]:
    """按文件名/内容粗判每个文件属于哪一类导出。"""
    found: List[dict] = []
    for path in paths:
        if not path or not os.path.isfile(path):
            continue
        low = _basename(path).lower()
        try:
            from src.pick import _looks_like_drill
        except Exception:  # noqa: BLE001
            _looks_like_drill = lambda p: False  # noqa: E731
        kind = None
        if low.endswith(".brd"):
            # .brd 不是"报告" —— 它是设计版本差异比较用的对比板。
            # 若把它当报告解析, 整份二进制会变成标题 (实测踩过)。
            kind = "brd"
        elif low in ("evan_components.csv", "evan_holes.csv", "evan_pads.csv"):
            kind = "skill"
        elif low.startswith("place_txt"):
            kind = "placement"
        elif low.endswith((".drl", ".nc", ".xln")) or _looks_like_drill(path):
            kind = "excellon"
        elif low.endswith(".ipc"):
            kind = "ipc356"
        elif low.endswith((".rpt", ".drc")) or "drc" in low:
            kind = "drc"
        elif "pstxnet" in low or "netlist" in low or low.endswith(".net"):
            kind = "netlist"
        elif low.endswith((".csv", ".txt")):
            kind = "report"
        if kind:
            found.append({"path": os.path.abspath(path), "kind": kind})
    return found


def _auto_dirs(brd_path: str, extra_dirs: Iterable[str]) -> List[str]:
    dirs = []
    if brd_path:
        dirs.append(os.path.dirname(os.path.abspath(brd_path)))
    dirs.extend(d for d in extra_dirs if d and os.path.isdir(d))
    out = []
    for d in dirs:
        if d not in out:
            out.append(d)
        sub = os.path.join(d, "allegro_bridge")
        if os.path.isdir(sub) and sub not in out:
            out.append(sub)
    return out


def _scan_dir(d: str) -> List[dict]:
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return []
    return discover(os.path.join(d, n) for n in names)


def load_exports(brd_path: str, files: Optional[Iterable[str]] = None,
                 extra_dirs: Iterable[str] = ()) -> dict:
    """读入所有可用的 Allegro 导出文件, 汇总成一份结构化数据。"""
    result: dict = {
        "ok": True, "files": [], "errors": [],
        "components": [], "holes": [], "pads": [],
        "drc": None, "netlist": None, "ipc356": None, "reports": [],
        "kinds": {},
    }

    if files:
        candidates = discover(files)
        # 用户显式给了文件但没认出来: 也要尝试按内容解析, 不能默默丢掉。
        # 但 .brd 例外 —— 它是对比板 (由 db.design_difference 自己读), 当报告解析
        # 只会把二进制变成"标题"。
        for path in files:
            if not path or not os.path.isfile(path) or path.lower().endswith(".brd"):
                continue
            if all(c["path"] != os.path.abspath(path) for c in candidates):
                candidates.append({"path": os.path.abspath(path), "kind": "report"})
    else:
        candidates = []
        for d in _auto_dirs(brd_path, extra_dirs):
            candidates.extend(_scan_dir(d))

    # 坐标类文件交给 src.pick (它已经处理了 SKILL CSV / Placement / Excellon 的差异)
    coord_files = [c["path"] for c in candidates if c["kind"] in ("skill", "placement", "excellon")]
    if coord_files:
        try:
            from src.pick import build_inventory

            inv = build_inventory(brd_path, files=coord_files)
            result["components"] = inv.get("components") or []
            result["holes"] = inv.get("holes") or []
            result["pads"] = inv.get("pads") or []
            for src in inv.get("sources") or []:
                result["files"].append({
                    "file": src.get("file"), "kind": src.get("kind", "coord"),
                    "count": src.get("count", 0), "note": src.get("unit", ""),
                    "parsed": True,
                })
            for err in inv.get("errors") or []:
                result["errors"].append(err)
        except Exception as exc:  # noqa: BLE001
            result["errors"].append(f"坐标文件解析失败: {exc}")

    for cand in candidates:
        kind = cand["kind"]
        path = cand["path"]
        if kind in ("skill", "placement", "excellon"):
            continue
        if kind == "brd":
            # 对比板不下在这里解析, 只登记一下; db.design_difference 会用
            # Context.export_files 自己读第二个 .brd。
            result["files"].append({
                "file": _basename(path), "kind": "brd", "count": 0,
                "note": "对比板 (供设计版本差异比较)", "parsed": False,
            })
            continue
        try:
            if kind == "ipc356":
                got = parse_ipc356(path)
            elif kind == "netlist":
                got = parse_netlist(path)
            elif kind == "drc":
                got = parse_drc_report(path)
            else:
                text = read_text(path)
                if _looks_like_drc(text) and re.search(r"\.rpt$", path, re.I):
                    got = parse_drc_report(path)
                elif _looks_like_netlist(text):
                    got = parse_netlist(path)
                else:
                    got = parse_allegro_report(path)
        except Exception as exc:  # noqa: BLE001
            result["errors"].append(f"{_basename(path)}: {exc}")
            continue

        if not got.get("ok"):
            result["errors"].append(f"{_basename(path)}: {got.get('reason', '解析失败')}")
            result["files"].append({"file": _basename(path), "kind": got.get("kind", kind),
                                    "count": 0, "note": got.get("reason", ""),
                                    "parsed": False, "path": path})
            continue

        if got["kind"] == "drc":
            if result["drc"] is None:
                result["drc"] = got
            else:  # 多份 DRC 报告: 合并条目
                result["drc"]["items"].extend(got["items"])
                result["drc"]["count"] = len(result["drc"]["items"])
                for cls, cnt in got["by_class"].items():
                    result["drc"]["by_class"][cls] = result["drc"]["by_class"].get(cls, 0) + cnt
            count = got["count"]
        elif got["kind"] == "netlist":
            result["netlist"] = result["netlist"] or got
            count = got["count"]
        elif got["kind"] == "ipc356":
            result["ipc356"] = result["ipc356"] or got
            count = got["point_count"]
        else:
            result["reports"].append(got)
            count = sum(len(s["rows"]) for s in got.get("sections") or [])
        result["files"].append({"file": _basename(path), "kind": got["kind"],
                                "count": count, "note": "", "parsed": True, "path": path})

    # 没给文件时自动发现: 只在同目录/输出目录里找, 找不到不算错误
    if not candidates:
        result["errors"] = []

    result["kinds"] = {}
    for f in result["files"]:
        result["kinds"][f["kind"]] = result["kinds"].get(f["kind"], 0) + 1
    return result
