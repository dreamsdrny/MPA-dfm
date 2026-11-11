"""第 8 类: 数据库与设计数据导出。

对应 PCB Editor / Allegro 里"把设计数据库里的东西倒出来"的那一排功能:
Netlist Export / Symbol Spreadsheet / Placement Export / Parameters Export /
Annotation Export / Techfile Export / Design Difference / Report Export。

与其它类不同, 这一类几乎不做"判定", 只做"导出 + 说清导出边界"。但纪律不变
—— **缺数据 ≠ 通过**:

  * 导不出来的, 状态是 `unavailable`, 并写明"缺什么、在 Allegro 哪一步补";
  * 导出来了但内容不完整的 (netlist.json 的引脚全空、器件表没有封装),
    结论与 warning 必须说清"哪一列是空的、为什么空";
  * 绝不写一张空表让用户以为"板上没有网络/没有器件"。

数据现实 (为什么有些列注定是空的)
--------------------------------
  * 网络名: 归档 `objects/Net.xml` 的 `<o N="GND"/>` 节点名 (可信), 不用二进制
    文本正则去猜; 但 **网络到引脚的连接** .brd 里没有, 只有 Allegro 导出的
    网表 (pstxnet.dat) 才有;
  * 器件位号: 归档 `objects/Part.xml`; 封装/料号在 PartDefn/Gate 里, 结构随版本
    变, 本模块只做**防御式探查**, 取不到就留空并说明缺什么;
  * 器件 XY/旋转角: 不在 .brd 里, 必须 Placement / SKILL 导出;
  * 文字/标注: 快照只带图形, 不带"位号 <-> 文字"的绑定关系, 所以这些文字
    **不能**当位号位置用;
  * techfile: Allegro 的 techfile 是私有二进制/加密格式, 第三方工具生成不了。
    本模块输出的是**可读约束清单** `constraints.tcf`, 要写回设计请用
    `src/skill_generator.py` 生成的 SKILL 脚本。
"""
import csv
import json
import os
import tempfile
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple
from xml.etree import ElementTree as ET

from src.brd_analysis import facts, geom
from src.brd_analysis.context import Context
from src.brd_analysis.model import (INFO, PASS, WARN, Capability, Result,
                                    unavailable)

# 位号前缀分类口径与 src/brd_components.py 保持一致 (那边是唯一定义处, 这里只引用;
# 万一被改坏也不会让本模块 import 失败, 退回本地副本并照同口径分类)。
_FALLBACK_PREFIX_CATEGORY = {
    "R": "电阻", "RN": "电阻排", "RP": "电阻排", "VR": "可调电阻",
    "C": "电容", "CP": "钽电容",
    "L": "电感", "FB": "磁珠", "F": "保险丝", "T": "变压器",
    "U": "IC芯片", "IC": "IC芯片", "Q": "三极管/MOS管",
    "D": "二极管", "LED": "发光二极管",
    "X": "晶振", "Y": "晶振", "OSC": "晶振",
    "J": "连接器/接插件", "P": "连接器/接插件",
    "CN": "连接器/接插件", "CON": "连接器/接插件",
    "SW": "开关", "K": "继电器", "S": "开关",
    "TP": "测试点", "MH": "安装孔", "H": "安装孔", "FH": "安装孔",
    "B": "电池", "BT": "电池", "BZ": "蜂鸣器", "M": "电机", "MOV": "压敏电阻",
}


def _prefix_of(refdes: str) -> str:
    """
    功能：提取PCB位号中的字母前缀。
    示例：R3438 -> R; FB44 -> FB; U93 -> U
    逻辑：提取数字前面的那段连续字母。
    """
    import re  # 导入正则表达式模块（放在函数内属于懒加载，减少启动依赖）

    try:
        # 【优先路径】尝试从项目核心模块 src.brd_components 导入标准实现
        # 这样做的目的是保证全项目的位号解析逻辑（口径）是统一的
        from src.brd_components import _prefix_of as impl

        # 如果导入成功，直接调用核心模块的函数并返回结果
        return impl(refdes)

    except Exception:  # noqa: BLE001
        # 【兜底路径】如果核心模块缺失、报错或不可用，则捕获异常并执行以下本地代码
        # noqa: BLE001 是告诉代码检查工具：我知道捕获所有异常不规范，但这里需要这样做

        # 本地简易实现：使用正则提取前缀
        # r"^([A-Za-z]+)\d" 表示匹配以字母开头、且字母后紧跟数字的部分
        # refdes or "" 防止传入 None 导致报错
        m = re.match(r"^([A-Za-z]+)\d", refdes or "")
        
        # 如果匹配成功(m存在)，提取第一个捕获组(字母部分)并转为大写；否则返回空字符串
        return m.group(1).upper() if m else ""


def _category_of(prefix: str) -> str:
    try:
        from src.brd_components import _PREFIX_CATEGORY as table

        return table.get(prefix, prefix or "未知前缀")
    except Exception:  # noqa: BLE001
        return _FALLBACK_PREFIX_CATEGORY.get(prefix, prefix or "未知前缀")


# ==========================================================================
# 公共小工具 (输出目录 / 写文件 / 取值格式化)
# ==========================================================================
def _out_dir(ctx: Context) -> str:
    """输出根目录: ctx.out_dir -> .brd 所在目录 -> 系统临时目录。

    多一道保护: 如果推出来的目录是盘根 (例如 .brd 直接放在 D:\\ 下, dirname 得到
    "D:\\"), 就改用系统临时目录 —— 产物落到盘根既难找又容易污染, 不是用户想要的。
    """
    base = (ctx.out_dir or "").strip()
    if not base and ctx.brd_path:
        base = os.path.dirname(os.path.abspath(ctx.brd_path))
    if base:
        abs_base = os.path.abspath(base)
        if os.path.dirname(abs_base) != abs_base:      # 不是盘根/根目录
            return abs_base
    return tempfile.gettempdir()


def _db_dir(ctx: Context) -> str:
    """本类统一的产物目录 <out>/database/。"""
    path = os.path.join(_out_dir(ctx), "database")
    os.makedirs(path, exist_ok=True)
    return path


def _cell(value: Any) -> Any:
    """CSV 单元格取值: None -> 空串, 列表 -> 顿号连接, 浮点 -> 去尾零。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    if isinstance(value, (list, tuple)):
        return "、".join(str(_cell(v)) for v in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return value


def _write_rows_csv(path: str, columns: List[str], rows: List[dict]) -> str:
    """写 CSV: utf-8-sig (Excel 直接双击不乱码) + newline=""。"""

    # 1. 确保父目录存在：
    #    - os.path.abspath(path)     → 把相对路径转成绝对路径，避免 cwd 变化导致 dirname 为空
    #    - os.path.dirname(...)      → 取出目录部分（例："/tmp/out/data.csv" → "/tmp/out"）
    #    - os.makedirs(..., exist_ok=True) → 递归创建多级目录；已存在不抛异常
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

    # 2. 以写模式打开文件：
    #    - encoding="utf-8-sig"：UTF-8 带 BOM，用 Excel 双击打开不会中文乱码
    #    - newline=""：交给 csv 模块自己处理换行，避免 Windows 上每行多一个空行
    with open(path, "w", encoding="utf-8-sig", newline="") as f:

        # 3. DictWriter：按“列名 → 字典键”的对应关系写行
        #    - fieldnames=list(columns)：复制一份列表，防止后续被意外修改
        #    - extrasaction="ignore"：row 里有但 columns 里没有的键，直接丢弃（不报错）
        writer = csv.DictWriter(f, fieldnames=list(columns), extrasaction="ignore")

        # 4. 先写表头（就是 columns 里的列名）
        writer.writeheader()

        # 5. 逐行写入：只挑 columns 中列出的键，缺失的键 row.get 返回 None
        #    _cell(...) 是单元格转换辅助函数（比如 None → ""，日期格式化，去换行等）
        for row in rows:
            writer.writerow({col: _cell(row.get(col)) for col in columns})

    # 6. 返回写入路径，方便调用方链式使用（如 logger 打印 / 前端下载）
    return path


def _write_json_file(path: str, obj: Any) -> str:
    """把任意 Python 对象序列化为格式化 JSON 文件。"""

    # 1. 同上：确保父目录存在（支持嵌套路径）
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

    # 2. 以 UTF-8 写文件（注意这里不带 BOM，JSON 规范不接受 BOM）
    with open(path, "w", encoding="utf-8") as f:

        # 3. json.dump 参数说明：
        #    - indent=2        ：缩进 2 空格，便于人读/代码评审 diff
        #    - ensure_ascii=False：中文直接输出为中文，而不是 \uXXXX
        #    - default=str     ：遇到 JSON 不认识的对象（datetime、Decimal、Path 等）
        #                        自动 str() 兜底，避免序列化报错
        json.dump(obj, f, indent=2, ensure_ascii=False, default=str)

    # 4. 返回路径，便于调用方复用
    return path


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _fmt_value(value: Any, unit: str = "") -> str:
    """指标/差异表里的人类可读值; None 明确写"未取到"而不是留空。"""
    if value is None:
        return "未取到"
    if isinstance(value, (list, tuple)):
        text = "、".join(str(v) for v in value)
    elif isinstance(value, float):
        text = f"{value:.4f}".rstrip("0").rstrip(".")
    elif isinstance(value, bool):
        text = "是" if value else "否"
    else:
        text = str(value)
    return f"{text}{unit}" if unit and text != "未取到" else text


def _num(value: Any) -> Optional[float]:
    """能当数字比就返回 float, 否则 None (差异表用它区分"没取到"和"0")。"""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


# ==========================================================================
# 8.1 Netlist Export —— 网络表导出
# ==========================================================================
def _net_pin_map(ctx: Context) -> Dict[str, List[str]]:
    """从导入的网表 (pstxnet.dat / 'NET: pin' 文本) 取网络->引脚。"""
    # 取 netlist，缺失/为 None 时兜底成 {}
    netlist = ctx.exports.get("netlist") or {}
    # 取 nets，仅当 netlist 是 dict 才访问，否则 None
    nets = netlist.get("nets") if isinstance(netlist, dict) else None
    # nets 不是 dict 视为无网表，返回空 dict（降级不抛错）
    if not isinstance(nets, dict):
        return {}
    # 归一化：键值统一转 str；值为 None 当空列表
    return {str(k): [str(p) for p in (v or [])] for k, v in nets.items()}


def cap_netlist(ctx: Context) -> Result:
    cap = CATALOG_MAP["db.netlist"]
    names = facts.net_names(ctx)
    if not names:
        res = unavailable(
            cap, "objects/Net.xml 缺失, 网络名不可得",
            "Allegro: Tools > Reports > Netlist",
            detail={"net_xml_found": False, "brd_zip_objects": len(ctx.objects)})
        res.metric("网络数", ctx.design.get("net_count"), "个",
                   "二进制 Net.xml 节点计数 (归档缺失, 低置信)", "low")
        return res

    res = Result(cap, PASS, "")
    dpairs = facts.diff_pair_names(ctx)
    guessed = facts.diff_pair_guess(names)
    pair_of: Dict[str, str] = {}
    for name in dpairs:
        pair_of.setdefault(name, name)
    for p_name, n_name in guessed:
        label = f"{p_name} / {n_name} (按 _P/_N 推断)"
        pair_of.setdefault(p_name, label)
        pair_of.setdefault(n_name, label)
    pg = set(facts.power_ground_nets(names))
    pin_map = _net_pin_map(ctx)

    rows: List[dict] = []
    for name in names:
        rows.append({"网络名": name, "差分对": pair_of.get(name, ""),
                     "电源地分类": "电源地" if name in pg else "信号"})
    res.add_all(rows, ["网络名", "差分对", "电源地分类"])

    net_json: Dict[str, List[str]] = {name: pin_map.get(name, []) for name in names}
    db_dir = _db_dir(ctx)
    csv_path = _write_rows_csv(os.path.join(db_dir, "netlist.csv"),
                               ["网络名", "差分对", "电源地分类"], rows)
    json_path = _write_json_file(os.path.join(db_dir, "netlist.json"), net_json)
    res.artifact("netlist_csv", csv_path, "网络清单 (网络名/差分对/电源地分类)")
    res.artifact("netlist_json", json_path, "{网络名: [引脚...]}")

    pin_nets = sum(1 for v in net_json.values() if v)
    res.metric("网络数", len(names), "个", "objects/Net.xml 节点名", "high")
    res.metric("差分对数", len(dpairs), "对", "objects/DiffPair.xml 节点名", "high")
    if guessed:
        res.metric("按 _P/_N 推断的配对", len(guessed), "对",
                   "Net.xml 名字后缀配对 (仅提示, 不是设计里的差分对定义)", "low")
    res.metric("电源/地网络", len(pg), "个", "网络名前缀规则 (GND/VCC/…)", "medium")
    res.metric("有引脚连接的网络", pin_nets, "个",
               "导入的网表文件 (pstxnet.dat / NET: 文本)", "high" if pin_nets else "")
    if pin_nets:
        res.metric("引脚连接总数", sum(len(v) for v in net_json.values()), "个",
                   "导入的网表文件")
    net_count = ctx.design.get("net_count")
    if net_count and int(net_count) != len(names):
        res.warn(f"二进制里的网络数为 {net_count}, Net.xml 节点名为 {len(names)} 个, "
                 f"两者不一致 (以 Net.xml 为准, 二进制计数可能含未保存的临时网络)")

    if pin_nets:
        res.summary = (f"导出 {len(names)} 个网络 (电源/地 {len(pg)} 个, 差分对 "
                       f"{len(dpairs)} 对), 其中 {pin_nets} 个网络带引脚连接")
    else:
        res.summary = (f"导出 {len(names)} 个网络 (电源/地 {len(pg)} 个, 差分对 "
                       f"{len(dpairs)} 对); netlist.json 里引脚列表全为空")
        res.need("每个网络的引脚连接 (哪个器件哪个脚接在这个网上)",
                 "Allegro: Tools > Reports > Netlist 或 File > Export > Netlist 导出 "
                 "pstxnet.dat, 再用 --analyze-files 传给工具")
        res.warn("netlist.json 只有网络名没有引脚: .brd 的 Net.xml 只存网络对象, "
                 "连接关系在设计数据库里, 必须由 Allegro 导出网表才拿得到")
    if not dpairs and not guessed:
        res.warn("没有读到任何差分对定义 (DiffPair.xml 缺失且网络名里也没有 _P/_N 配对)")
    res.source("objects/Net.xml 节点名 (网络名) + 导入的网表文件 (引脚连接)")
    return res


# ==========================================================================
# 8.2 Symbol Spreadsheet —— 器件符号信息
# ==========================================================================
# 封装/料号可能出现在这些属性名里 (不同版本/流程写法不同, 用"包含"匹配)
_PKG_KEY_HINTS = ("PACKAGE", "PKG", "FOOTPRINT", "PART_NUMBER", "PARTNUM",
                  "PART_NAME", "PART_TYPE", "DEVICE", "SYM_NAME", "SYMBOL")
_REFDES_KEY_HINTS = ("REFDES", "REF_DES", "REFERENCE", "REF_NAME")


def _scan_object_fields(data: bytes) -> Tuple[List[dict], str]:
    """防御式扫一个归档 XML: 返回 ([{name, fields}], error)。

    只认两种可靠的结构 (Allegro 的归档对象都用它):
        <o N="节点名"> ... <a N="属性名"><v V="值"/></a> ... </o>
    其它写法一律不猜: 拿不到就是拿不到, 由调用方如实留空。
    """
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        return [], f"XML 解析失败: {exc}"
    nodes: List[dict] = []
    for node in root.iter():
        if node.tag != "o":
            continue
        name = (node.get("N") or "").strip()
        fields: Dict[str, str] = {}
        for key, value in (node.attrib or {}).items():
            if key == "N" or not value:
                continue
            fields[key.strip().upper()] = str(value).strip()
        for attr in node.findall("a"):
            key = (attr.get("N") or "").strip().upper()
            if not key:
                continue
            value = ""
            v = attr.find("v")
            if v is not None:
                value = (v.get("V") or v.get("G") or v.text or "").strip()
            if not value:
                value = (attr.text or "").strip()
            if value:
                fields[key] = value
        if fields or name:
            nodes.append({"name": name, "fields": fields})
    return nodes, ""


def _pick_field(fields: Dict[str, str], hints: Iterable[str]) -> str:
    for key in sorted(fields):
        up = key.upper()
        if any(h in up for h in hints):
            return fields[key]
    return ""


def _package_map(objects: Dict[str, bytes],
                 refdes: Iterable[str]) -> Dict[str, Any]:
    """从 PartDefn.xml / Gate.xml 防御式取"位号 -> 封装名"。

    返回 {by_refdes, entries, notes}: entries 是结构探查结果 (给用户看"这些条目
    里到底有什么"), notes 是没映射上的原因 —— 不做静默丢弃, 也不瞎猜字段。
    """
    wanted = set(refdes or [])
    by_refdes: Dict[str, dict] = {}
    entries: List[dict] = []
    notes: List[str] = []
    for entry_name in ("objects/PartDefn.xml", "objects/Gate.xml"):
        data = (objects or {}).get(entry_name)
        if not data:
            continue
        nodes, error = _scan_object_fields(data)
        stat = {"条目": entry_name, "节点数": len(nodes),
                "有封装字段的节点": 0, "映射到位号的节点": 0,
                "字段名示例": "", "错误": error}
        keys_seen: List[str] = []
        for node in nodes:
            fields = node["fields"] or {}
            for key in fields:
                if key not in keys_seen:
                    keys_seen.append(key)
            pkg = _pick_field(fields, _PKG_KEY_HINTS)
            if not pkg:
                continue
            stat["有封装字段的节点"] += 1
            ref = _pick_field(fields, _REFDES_KEY_HINTS)
            target = ""
            if ref and ref in wanted:
                target = ref
            else:
                plain = node["name"]
                for sep in (".", "/", ":"):
                    if sep in plain:
                        plain = plain.split(sep, 1)[0]
                if plain in wanted:
                    target = plain
            if not target:
                continue
            stat["映射到位号的节点"] += 1
            by_refdes.setdefault(target, {"封装": pkg, "来源": entry_name})
        stat["字段名示例"] = "、".join(keys_seen[:12])
        entries.append(stat)
        if nodes and not stat["有封装字段的节点"]:
            notes.append(f"{entry_name} 有 {len(nodes)} 个节点, 但属性名里没有封装/料号关键字 "
                         f"({'、'.join(keys_seen[:8]) or '无属性'}), 未取到封装")
    return {"by_refdes": by_refdes, "entries": entries, "notes": notes}


def cap_symbol_spreadsheet(ctx: Context) -> Result:
    cap = CATALOG_MAP["db.symbol_spreadsheet"]
    comp = facts.components(ctx)
    if not comp.get("ok"):
        return unavailable(
            cap, f"没有器件位号: {comp.get('reason', 'BRD 无 objects/Part.xml')}",
            "确认 .brd 是真实 PCB 设计 (FAB 拼板图/纯板框图纸没有 Part.xml); "
            "或提供 BOM/ERP 导出的器件清单")
    res = Result(cap, PASS, "")
    refdes = list(comp.get("refdes") or [])
    pkg = _package_map(ctx.objects, refdes)
    by_refdes = pkg["by_refdes"]

    rows: List[dict] = []
    for ref in refdes:
        prefix = _prefix_of(ref)
        info = by_refdes.get(ref) or {}
        package = info.get("封装", "")
        src = "objects/Part.xml 位号"
        if package:
            src += f" + {info.get('来源', '')} 封装"
        else:
            src += " (封装未取到)"
        rows.append({"位号": ref, "前缀": prefix, "类别": _category_of(prefix),
                     "封装": package, "来源": src})
    res.add_all(rows, ["位号", "前缀", "类别", "封装", "来源"])

    db_dir = _db_dir(ctx)
    csv_path = _write_rows_csv(os.path.join(db_dir, "symbol_spreadsheet.csv"),
                               ["位号", "前缀", "类别", "封装", "来源"], rows)
    res.artifact("symbol_csv", csv_path, "器件符号表 (位号/前缀/类别/封装/来源)")

    with_pkg = sum(1 for r in rows if r["封装"])
    res.metric("器件数", len(refdes), "个", "objects/Part.xml 位号清单", "high")
    for label, count in list((comp.get("by_category") or {}).items())[:10]:
        res.metric(f"其中 {label}", count, "个", "位号前缀分类 (口径同 brd_components)")
    res.metric("取到封装的器件", with_pkg, "个",
               "objects/PartDefn.xml / objects/Gate.xml 防御式探查",
               "high" if with_pkg else "")
    res.extra["partdefn_scan"] = pkg["entries"]

    if with_pkg:
        res.summary = (f"导出 {len(refdes)} 个器件的位号与分类, 其中 {with_pkg} 个取到封装名; "
                       f"封装来源: {'、'.join(sorted({r['来源'] for r in rows if r['封装']}))}")
    else:
        res.summary = (f"导出 {len(refdes)} 个器件的位号与分类; 封装列为空 —— "
                       f".brd 的 Part.xml 只有位号, 封装/料号需要 PartDefn/ERP 数据")
        res.need("封装名 (Footprint) 与料号",
                 "提供含 objects/PartDefn.xml / objects/Gate.xml 的 .brd, "
                 "或从 ERP/BOM 导出器件清单 (工厂通常从 BOM 拿封装)")
        res.warn(".brd 的 Part.xml 只有位号; 封装/料号需要 PartDefn/ERP 数据 —— "
                 "本表封装列留空是「缺数据」, 不是「没有封装」")
    for scan in pkg["entries"]:
        if scan.get("错误"):
            res.warn(f"{scan['条目']} {scan['错误']}, 封装列按缺失处理")
    for note in pkg["notes"]:
        res.warn(note)
    res.source("objects/Part.xml 位号清单 (分类口径同 src/brd_components.py)")
    if pkg["entries"]:
        res.source("objects/PartDefn.xml / objects/Gate.xml (防御式探查, 只取明确带封装字段的节点)")
    return res


# ==========================================================================
# 8.3 Placement Export —— 元件布局数据
# ==========================================================================
_PLACE_COLUMNS = ["位号", "X", "Y", "旋转", "面别"]


def _placement_rows(comps: List[dict]) -> List[dict]:
    rows = []
    for c in comps:
        rows.append({
            "位号": c.get("refdes") or "",
            "X": (round(float(c["x_mm"]), 4) if c.get("x_mm") is not None else None),
            "Y": (round(float(c["y_mm"]), 4) if c.get("y_mm") is not None else None),
            "旋转": c.get("rotation", ""),
            "面别": (c.get("side") or "").upper(),
        })
    rows.sort(key=lambda r: str(r["位号"]))
    return rows


def _placement_txt(rows: List[dict], sources: List[str]) -> str:
    """Allegro 风格的 Placement 文本: REFDES X Y ROTATION SIDE (空格对齐)。

    注意: 这是"按 Allegro 导出的列顺序排版的纯文本", 不是 Allegro 原生命令产物,
    要回流设计请用工具生成的 SKILL 脚本。
    """
    lines = [
        "# Evan_BRD Placement Export (列顺序与 Allegro File > Export > Placement 一致)",
        "# 单位: millimeter",
        "# 来源: " + ("、".join(sources) if sources else "导入的坐标文件"),
        "# REFDES 旋转角为度; SIDE 为 TOP/BOTTOM",
        f"{'REFDES':<12}{'X':>12}{'Y':>12}{'ROTATION':>12}  {'SIDE':<6}",
    ]
    for row in rows:
        x = row["X"]
        y = row["Y"]
        try:
            rot = float(row["旋转"])
        except (TypeError, ValueError):
            rot = 0.0
        lines.append(f"{str(row['位号']):<12}{x if x is not None else '':>12}"
                     f"{y if y is not None else '':>12}{rot:>12.3f}  "
                     f"{str(row['面别'] or 'TOP'):<6}")
    return "\n".join(lines) + "\n"


def cap_placement(ctx: Context) -> Result:
    cap = CATALOG_MAP["db.placement"]
    comps = ctx.exports.get("components") or []
    if not comps:
        res = unavailable(cap, "没有器件 XY 坐标 (Placement / SKILL 坐标文件缺失)",
                          ctx.howto_components_xy(),
                          detail={"has_components_xy": False})
        comp = facts.components(ctx)
        if comp.get("ok"):
            res.metric("器件位号数", comp.get("count"), "个", "objects/Part.xml", "high")
        return res

    rows = _placement_rows(comps)
    db_dir = _db_dir(ctx)
    csv_path = _write_rows_csv(os.path.join(db_dir, "placement.csv"),
                               _PLACE_COLUMNS, rows)
    sources = sorted({str(c.get("source") or "") for c in comps if c.get("source")})
    txt_path = os.path.join(db_dir, "placement.txt")
    with open(txt_path, "w", encoding="utf-8", newline="") as f:
        f.write(_placement_txt(rows, sources))
    res = Result(cap, PASS, "")
    res.add_all(rows, _PLACE_COLUMNS)
    res.artifact("placement_csv", csv_path, "元件布局坐标 (位号/X/Y/旋转/面别)")
    res.artifact("placement_txt", txt_path,
                 "Allegro 风格 Placement 文本 (REFDES X Y ROTATION SIDE)")

    xs = [r["X"] for r in rows if r["X"] is not None]
    ys = [r["Y"] for r in rows if r["Y"] is not None]
    sides: Dict[str, int] = {}
    for r in rows:
        key = r["面别"] or "未标注"
        sides[key] = sides.get(key, 0) + 1
    res.metric("有坐标的器件", len(rows), "个", "导入的 Placement/SKILL 坐标文件", "high")
    if xs and ys:
        res.metric("布局范围 X", f"{min(xs):.3f} ~ {max(xs):.3f}", "mm", "导入的坐标文件")
        res.metric("布局范围 Y", f"{min(ys):.3f} ~ {max(ys):.3f}", "mm", "导入的坐标文件")
        res.metric("布局跨度", f"{max(xs) - min(xs):.3f} × {max(ys) - min(ys):.3f}", "mm",
                   "导入的坐标文件")
    for side, count in sorted(sides.items()):
        res.metric(f"{side} 面器件", count, "个", "导入的坐标文件")
    res.summary = (f"导出 {len(rows)} 个器件的坐标 ("
                   f"{' / '.join(f'{k} {v}' for k, v in sorted(sides.items()))}), "
                   f"CSV + Allegro 风格 TXT 各一份")
    comp = facts.components(ctx)
    if comp.get("ok") and comp.get("count") != len(rows):
        res.warn(f"Part.xml 有 {comp['count']} 个位号, 坐标文件只有 {len(rows)} 个, "
                 f"两者不一致 (可能有未摆放器件, 或坐标文件导出范围不同)")
    res.warn("placement.txt 是按 Allegro Placement 的列顺序排版的纯文本, 不是 Allegro "
             "原生命令产物; 要回流到设计请用工具的 SKILL 脚本 (--export-skill)")
    res.source("导入的 Placement / SKILL 坐标文件: " + ("、".join(sources) or "未标注来源"))
    return res


# ==========================================================================
# 8.4 Parameters Export —— 设计参数
# ==========================================================================
def _dfm_field_tables() -> Tuple[Dict[str, str], Dict[str, str]]:
    """(字段名 -> 中文标签, 字段名 -> 单位), 直接复用 src/dfm_inputs.py 的定义。"""
    from src.dfm_inputs import DFM_FIELDS, FIELD_META

    labels = {key: label for _cat, key, label, _default in DFM_FIELDS}
    units = {key: str((FIELD_META.get(key) or {}).get("unit") or "")
             for key in labels}
    return labels, units


def cap_parameters(ctx: Context) -> Result:
    cap = CATALOG_MAP["db.parameters"]
    doc = ctx.design
    if not doc.get("ok"):
        return unavailable(cap, f"BRD 解析失败或文件不存在: {ctx.design_error or '未知原因'}",
                          "确认 .brd 路径正确, 且是 Allegro 17.x 及以上 (含 XML 归档) 的版本")
    try:
        from src.dfm_inputs import brd_design_values
        labels, units = _dfm_field_tables()
    except Exception as exc:  # noqa: BLE001  口径表不可用时必须说清, 不能自己编
        return unavailable(cap, f"无法加载 DFM 字段口径 (src/dfm_inputs.py): {exc}",
                          "确认 src/dfm_inputs.py 完整 (字段名/单位以它为唯一口径)")

    values, sources = brd_design_values(doc)
    if not values:
        res = unavailable(cap, "BRD 里没有可导出的设计参数值 (没有约束对象/钻孔表/器件清单)",
                          "确认 .brd 是 17.x 及以上版本; 或提供 Allegro 的约束报告/钻孔表")
        res.metric("层数", ctx.layer_count, "层", "BRD 预览元数据/SubClass.xml")
        return res

    rows: List[dict] = []
    for _cat, key, label, _default in _dfm_rows_order(labels):
        if key not in values:
            continue
        item = sources.get(key) or {}
        rows.append({"参数": label, "值": values[key], "单位": units.get(key, ""),
                     "来源": item.get("source", ""), "置信度": item.get("confidence", "")})
    # DFM 字段表之外的关键值 (有就带上, 标清出处与置信度)
    notes = ((doc.get("raw_note") or {}).get("notes") or {})
    extra_rows = [
        ("设计名", doc.get("design_name") or doc.get("board_name"), "", "Design.xml / 预览元数据", "high"),
        ("设计单位", doc.get("units"), "", doc.get("units_source"), "high"),
        ("成品铜厚", notes.get("copper_oz"), "oz", "制程说明文本", "low"),
        ("表面处理", "、".join(doc.get("surface_finishes") or []), "",
         "制程说明文本", "low"),
        ("阻抗控制要求", "有" if doc.get("impedance") else "未提及", "",
         "制程说明文本", "low"),
        ("钻孔跨度", " / ".join(doc.get("drill_spans") or []), "",
         "二进制钻孔表", "medium"),
        ("最大机械钻孔", _max_pth_drill(doc), "mm", "钻孔表 PTH 最大孔径", "medium"),
    ]
    for label, value, unit, source, confidence in extra_rows:
        if value in (None, "", []):
            continue
        rows.append({"参数": label, "值": value, "单位": unit,
                     "来源": source or "未解析到", "置信度": confidence})

    db_dir = _db_dir(ctx)
    json_path = _write_json_file(os.path.join(db_dir, "design_parameters.json"), {
        "board": ctx.board_name,
        "brd_path": ctx.brd_path,
        "generated_at": _now(),
        "unit_note": "长度单位 mm; 字段名与 src/dfm_inputs.brd_design_values() 完全一致",
        "values": values,
        "value_sources": sources,
        "brd_sources": doc.get("sources") or {},
        "extra_notes": {k: v for k, v in notes.items() if v not in (None, "")},
        "warnings": list(doc.get("warnings") or []),
    })
    csv_path = _write_rows_csv(os.path.join(db_dir, "design_parameters.csv"),
                               ["参数", "值", "单位", "来源", "置信度"], rows)
    constraint_rows = facts.constraint_rows(ctx)
    cons_path = ""
    if constraint_rows:
        cons_path = _write_rows_csv(os.path.join(db_dir, "constraint_table.csv"),
                                    ["类别", "约束集", "约束", "值", "单位"], constraint_rows)
    res = Result(cap, PASS, "")
    res.add_all(rows, ["参数", "值", "单位", "来源", "置信度"])
    res.artifact("parameters_json", json_path, "设计参数 + 每项出处 (JSON)")
    res.artifact("parameters_csv", csv_path, "设计参数 (参数/值/单位/来源/置信度)")
    if cons_path:
        res.artifact("constraint_table_csv", cons_path,
                     f"完整约束表 {len(constraint_rows)} 条 (类别/约束集/约束/值/单位)")
    res.extra["dfm_values"] = values
    res.extra["dfm_sources"] = sources
    res.extra["brd_sources"] = doc.get("sources") or {}

    def take(label: str, key: str, unit: str = "mm") -> None:
        if values.get(key) is not None:
            item = sources.get(key) or {}
            res.metric(label, values[key], unit, item.get("source", ""),
                       item.get("confidence", ""))

    take("层数", "layer_count", "层")
    take("板尺寸X", "board_size_x")
    take("板尺寸Y", "board_size_y")
    take("最小线宽", "min_line_width")
    take("最小线距", "min_spacing")
    take("最小颈宽", "min_neck_width")
    take("最小机械钻孔", "min_mech_drill")
    take("最小激光孔", "min_laser_via")
    take("单边孔环 (机械孔)", "min_annular_ring")
    take("单边孔环 (激光/盲埋孔)", "min_annular_ring_laser")
    take("板厚", "board_thickness")
    take("器件数", "comp_count", "个")
    if constraint_rows:
        res.metric("导出的约束条目", len(constraint_rows), "条",
                   "objects/PhysicalCSet.xml + objects/SpacingCSet.xml", "high")
    res.summary = (f"导出 {len(rows)} 项设计参数 (design_parameters.csv / .json, "
                   f"字段名与 dfm_inputs 一致) + {len(constraint_rows)} 条完整约束 "
                   f"(单独成表 constraint_table.csv, 与参数表分开以便按约束集查看); "
                   f"最小线宽 {_fmt_value(values.get('min_line_width'))}mm, "
                   f"最小线距 {_fmt_value(values.get('min_spacing'))}mm, "
                   f"最小钻孔 {_fmt_value(values.get('min_mech_drill'))}mm")
    for key, item in (doc.get("sources") or {}).items():
        conf = item.get("confidence") or ""
        res.source(f"{key}: {item.get('source') or '未标注'}"
                   + (f" (置信度 {conf})" if conf else ""))
    for warning in (doc.get("warnings") or [])[:6]:
        res.warn(warning)
    res.warn("设计参数里的板厚/铜厚来自制程说明文本, 置信度低; 介质厚度/Er 需要叠层报告")
    res.source("BRD 归档约束对象 + 钻孔表 + objects/Part.xml (口径复用 src/dfm_inputs.py)")
    return res


def _dfm_rows_order(labels: Dict[str, str]) -> List[tuple]:
    """保持 DFM_FIELDS 的原始顺序 (界面/报告里参数顺序一致)。"""
    try:
        from src.dfm_inputs import DFM_FIELDS
    except Exception:  # noqa: BLE001
        return [("", key, labels.get(key, key), "") for key in sorted(labels)]
    return list(DFM_FIELDS)


def _max_pth_drill(doc: dict) -> Optional[float]:
    sizes = [r.get("size_mm") for r in (doc.get("drill_rows") or [])
             if r.get("plating") == "PTH" and r.get("size_mm")]
    return round(max(sizes), 4) if sizes else None


# ==========================================================================
# 8.5 Annotation Export —— 标注/文字信息
# ==========================================================================
# 文字内容可能放在这些字段名里 (逐个小写变体试, 取不到就只统计图形数与位置)
_TEXT_FIELD_KEYS = ("text", "value", "label", "name", "str", "string", "content",
                    "txt", "text_value", "textvalue", "display", "label_text",
                    "refdes", "title")
_TEXT_HINTS = ("TEXT", "DIMENSION", "REFDES", "TITLE")


def _entry_text(entry: dict) -> str:
    """防御式取文字: 先看本层字段名, 再看一层嵌套字典; 取不到返回空串。"""
    if not isinstance(entry, dict):
        return ""
    for key, value in entry.items():
        if str(key).strip().lower() in _TEXT_FIELD_KEYS:
            if isinstance(value, str) and value.strip():
                return value.strip()
    for value in entry.values():
        if isinstance(value, dict):
            inner = _entry_text(value)
            if inner:
                return inner
    return ""


def _entry_anchor_mm(entry: dict, scale: float) -> Tuple[Optional[float], Optional[float]]:
    """文字的插入点 (快照 points 的第一个点) 换算成 mm; 没标定/没点则 None。"""
    if not scale:
        return None, None
    for point in entry.get("points") or []:
        if isinstance(point, dict) and point.get("x") is not None:
            try:
                return (round(float(point["x"]) * scale, 4),
                        round(float(point["y"]) * scale, 4))
            except (TypeError, ValueError):
                continue
    return None, None


def _annotation_rows(ctx: Context) -> Tuple[List[dict], dict]:
    """扫快照里的文字/标注图形。返回 (rows, stats)。

    取图形范围: geom.classify() 的 kind 为 text/other, 或 class/subclass 里含
    TEXT/DIMENSION/REFDES/TITLE。快照 entry 里 **没有** "这段文字属于哪个位号"
    的绑定字段, 所以本表只能给文字与位置, 不能当"位号位置"用。
    """
    scale = ctx.scale_mm_per_unit if ctx.has_scale else 0.0
    rows: List[dict] = []
    stats = {"候选图形": 0, "有文字": 0, "无文字": 0, "层": {},
             "类别": {}, "截断": False}
    limit = 5000
    for entry in ctx.raw_entries:
        if not isinstance(entry, dict):
            continue
        kind = geom.classify(entry)
        # 关键字比对前把空格/下划线去掉: "REF DES" 与 "REFDES" 都要认出来
        blob = (f"{entry.get('class') or ''} {entry.get('subclass') or ''}"
                .upper().replace(" ", "").replace("_", ""))
        hint = any(h in blob for h in _TEXT_HINTS)
        if kind not in ("text", "other") and not hint:
            continue
        stats["候选图形"] += 1
        text = _entry_text(entry)
        label = "文字/标注" if (kind == "text" or hint) else f"{kind or 'other'}图形"
        if text:
            stats["有文字"] += 1
        else:
            stats["无文字"] += 1
        layer = geom.layer_of(entry)
        stats["层"][layer] = stats["层"].get(layer, 0) + 1
        stats["类别"][label] = stats["类别"].get(label, 0) + 1
        if len(rows) >= limit:
            stats["截断"] = True
            continue
        x, y = _entry_anchor_mm(entry, scale)
        rows.append({"类别": label, "层": layer, "文字": text,
                     "位置X": x, "位置Y": y})
    return rows, stats


def cap_annotation(ctx: Context) -> Result:
    cap = CATALOG_MAP["db.annotation"]
    if not ctx.has_snapshot:
        return unavailable(cap, "该 .brd 没有可解析的内嵌设计快照, 读不到任何文字/标注图形",
                          "确认 .brd 内含设计快照; 或用 Allegro 的 Tools > Reports "
                          "导出图框/标注信息")
    rows, stats = _annotation_rows(ctx)
    if not stats["候选图形"]:
        return unavailable(cap, "快照里没有文字/标注类图形 "
                                "(kind=text/other 且 class/subclass 不含 TEXT/DIMENSION/REFDES/TITLE)",
                          "Allegro: 确认导出设计快照时包含 TEXT/DIMENSION/REFDES/TITLE 图形")
    res = Result(cap, INFO, "")
    res.add_all(rows, ["类别", "层", "文字", "位置X", "位置Y"])
    db_dir = _db_dir(ctx)
    csv_path = _write_rows_csv(os.path.join(db_dir, "annotation.csv"),
                               ["类别", "层", "文字", "位置X", "位置Y"], rows)
    res.artifact("annotation_csv", csv_path, "文字/标注图形 (类别/层/文字/位置X/位置Y)")

    res.metric("文字/标注候选图形", stats["候选图形"], "条",
               "内嵌快照 class/subclass + geom.classify()", "medium")
    res.metric("读到文字内容", stats["有文字"], "条", "entry 里 text/value/label 等字段", "low")
    res.metric("只统计图形与位置", stats["无文字"], "条",
               "entry 里没有可识别的文字字段", "medium")
    res.metric("涉及层/子类", len(stats["层"]), "个", "快照 class/subclass")
    for label, count in sorted(stats["类别"].items(), key=lambda kv: -kv[1])[:8]:
        res.metric(f"其中 {label}", count, "条", "geom.classify()")
    if stats["截断"]:
        res.warn(f"候选图形超过 {len(rows)} 条, 明细表只保留前 {len(rows)} 条 "
                 f"(统计数仍是全量)")
    if not ctx.has_scale:
        res.warn("快照坐标未标定 (换算不成 mm), 位置列留空")
    res.summary = (f"扫到 {stats['候选图形']} 条文字/标注候选图形 "
                   f"(读到文字 {stats['有文字']} 条, 仅图形 {stats['无文字']} 条)")
    if not stats["有文字"]:
        res.need("文字内容 (快照 entry 里没有可识别的文字字段)",
                 "Allegro: Tools > Reports 或 Assembly 图导出文字清单; "
                 "快照只带图形几何时不带文字时无法恢复")
    if stats["有文字"]:
        res.status = PASS
    res.warn("快照里没有位号与文字的绑定关系, 所以这些文字不能当位号位置用 "
             "(要位号坐标请用 db.placement)")

    # 结构探查: 把实际见过的字段名记下来, 便于下一步适配更多版本
    keys: List[str] = []
    for entry in ctx.raw_entries[:400]:
        if isinstance(entry, dict):
            for key in entry:
                if str(key) not in keys:
                    keys.append(str(key))
    res.extra["entry_fields_seen"] = keys
    res.extra["layer_stats"] = stats["层"]
    res.source("内嵌设计快照 (class/subclass 关键字 + geom.classify)")
    return res


# ==========================================================================
# 8.6 Techfile Export —— 约束技术文件
# ==========================================================================
def _constraint_sections(ctx: Context) -> Dict[str, Dict[str, dict]]:
    """把 PhysicalCSet / SpacingCSet 收成 {组: {约束集: {键: 值}}}。"""
    sections: Dict[str, Dict[str, dict]] = {}
    physical = ctx.design.get("physical_csets") or {}
    spacing = ctx.design.get("spacing_csets") or {}
    if physical:
        sections["Physical"] = {str(k): dict(v) for k, v in physical.items()
                                if isinstance(v, dict)}
    if spacing:
        sections["Spacing"] = {str(k): dict(v) for k, v in spacing.items()
                               if isinstance(v, dict)}
    return sections


def _tcf_value(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return ":".join(str(v) for v in value)      # VIA_LIST 用 Allegro 自己的分隔符
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def _render_tcf(sections: Dict[str, Any], meta: Dict[str, Any]) -> str:
    """渲染可读约束文件 (类 INI 纯文本)。

    sections 支持两种形态: {"Physical": {集: {...}}, "Spacing": {集: {...}}},
    或直接 {约束集: {...}} (当作 Physical, 方便单独喂一份约束字典自测)。
    """
    norm: Dict[str, Dict[str, dict]] = {}
    for key, value in (sections or {}).items():
        if key in ("Physical", "Spacing") and isinstance(value, dict):
            norm[key] = value
        elif isinstance(value, dict):
            norm.setdefault("Physical", {})[str(key)] = value

    lines: List[str] = [
        "#" + "=" * 74,
        "# Evan_BRD 约束导出 / constraints.tcf",
        f"# 板名: {meta.get('board', '')}",
        f"# 源文件: {meta.get('brd', '')}",
        f"# 生成时间: {meta.get('generated', '')}",
        f"# 生成来源: {meta.get('source', 'objects/PhysicalCSet.xml + objects/SpacingCSet.xml')}",
        "# 格式说明 (类 INI 纯文本, UTF-8):",
        "#   [Physical/<约束集名>] / [Spacing/<约束集名>]   段名",
        "#   段内一行一个约束:  键 = 值",
        "#   长度单位统一 mm; VIA_LIST 的值是 ':' 分隔的焊盘栈名列表",
        "#   以 # 开头的是注释行; 大写键名与 Allegro 约束对象里的属性名一致",
        "# 重要提示:",
        "#   这不是 Allegro 能直接 import 的 techfile。Allegro 的 techfile 是私有",
        "#   二进制/加密格式, 第三方工具无法生成。要自动把约束写回设计, 请用本工具",
        "#   src/skill_generator.py 生成的 SKILL 脚本 (--export-skill <目录>)。",
        "#" + "=" * 74,
        "[meta]",
        f"board = {meta.get('board', '')}",
        "units = mm",
        f"generated = {meta.get('generated', '')}",
        f"physical_csets = {len(norm.get('Physical') or {})}",
        f"spacing_csets = {len(norm.get('Spacing') or {})}",
    ]
    for group in ("Physical", "Spacing"):
        csets = norm.get(group) or {}
        for cset in sorted(csets):
            lines.append("")
            lines.append(f"[{group}/{cset}]")
            entry = csets[cset] or {}
            for key in sorted(entry):
                value = entry[key]
                if value is None:
                    lines.append(f"# {key} = (BRD 里未取到该属性)")
                    continue
                lines.append(f"{key} = {_tcf_value(value)}")
    return "\n".join(lines) + "\n"


def cap_techfile(ctx: Context) -> Result:
    cap = CATALOG_MAP["db.techfile"]
    sections = _constraint_sections(ctx)
    if not sections:
        return unavailable(
            cap, "BRD 归档里没有 objects/PhysicalCSet.xml / objects/SpacingCSet.xml, "
                 "没有约束可导出",
            "确认 .brd 是 17.x 及以上版本 (16.x 纯二进制无约束对象); "
            "或提供 Allegro 导出的约束报告/已有 .tcf")
    res = Result(cap, PASS, "")
    db_dir = _db_dir(ctx)
    path = os.path.join(db_dir, "constraints.tcf")
    text = _render_tcf(sections, {
        "board": ctx.board_name,
        "brd": ctx.brd_path,
        "generated": _now(),
        "source": "objects/PhysicalCSet.xml + objects/SpacingCSet.xml (从 .brd zip 归档直读)",
    })
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    res.artifact("constraint_tcf", path,
                 "可读约束清单 (类 INI 文本, 非 Allegro 可 import 的 techfile)")

    rows = facts.constraint_rows(ctx)
    res.add_all(rows, ["类别", "约束集", "约束", "值", "单位"])
    physical = sections.get("Physical") or {}
    spacing = sections.get("Spacing") or {}
    res.metric("Physical 约束集", len(physical), "个", "objects/PhysicalCSet.xml", "high")
    res.metric("Spacing 约束集", len(spacing), "个", "objects/SpacingCSet.xml", "high")
    res.metric("约束条目", len(rows), "条", "两类约束集逐属性摊平", "high")
    res.metric("文件行数", len(text.splitlines()), "行", path)
    res.summary = (f"导出 {len(physical)} 个 Physical + {len(spacing)} 个 Spacing 约束集, "
                   f"共 {len(rows)} 条约束 -> constraints.tcf")
    res.warn("这不是 Allegro 能直接 import 的 techfile: Allegro 的 techfile 是私有"
             "二进制/加密格式, 第三方工具生成不了")
    res.warn("要自动把约束写回设计, 请用本工具 src/skill_generator.py 生成的 SKILL 脚本 "
             "(--export-skill <目录>), 由 SKILL 调用 axl* 约束 API 写入")
    res.source("objects/PhysicalCSet.xml + objects/SpacingCSet.xml")
    return res


# ==========================================================================
# 8.7 Design Difference —— 设计版本差异比较
# ==========================================================================
def _diff_candidates(ctx: Context) -> Tuple[List[str], List[str]]:
    """从导出文件里挑对比板: .brd 结尾且不等于当前 .brd。返回 (候选, 同路径的)。"""
    current = os.path.abspath(ctx.brd_path) if ctx.brd_path else ""
    others: List[str] = []
    same: List[str] = []
    for path in ctx.export_files or []:
        if not path:
            continue
        abs_path = os.path.abspath(path)
        if not abs_path.lower().endswith(".brd"):
            continue
        if current and abs_path == current:
            same.append(abs_path)
        elif abs_path not in others:
            others.append(abs_path)
    return others, same


def _diff_row(item: str, cur: Any, ref: Any, unit: str = "",
              kind: str = "plain") -> dict:
    """一行差异。kind: count(数量) / looser_up(越大越宽松) / plain(仅报变化)。"""
    cur_n, ref_n = _num(cur), _num(ref)
    cur_txt, ref_txt = _fmt_value(cur, unit), _fmt_value(ref, unit)
    if cur is None and ref is None:
        return {"项目": item, "本版": "未取到", "对比版": "未取到",
                "差异": "无法比较", "结论": "两边都没有这个数据, 不等于「无差异」"}
    if cur is None or ref is None:
        return {"项目": item, "本版": cur_txt, "对比版": ref_txt,
                "差异": "仅本版有" if ref is None else "仅对比版有",
                "结论": "其中一边没有这个数据, 需人工核对"}
    if cur_n is not None and ref_n is not None:
        delta = round(cur_n - ref_n, 4)
        if delta == 0:
            return {"项目": item, "本版": cur_txt, "对比版": ref_txt,
                    "差异": "无差异", "结论": "一致"}
        diff_txt = f"{delta:+g}{unit}" if unit else f"{delta:+g}"
        if kind == "count":
            conclusion = f"增加 {abs(delta):g}" if delta > 0 else f"减少 {abs(delta):g}"
        elif kind == "looser_up":
            conclusion = "本版更宽松" if delta > 0 else "本版更严 (更难制造)"
        else:
            conclusion = "变大" if delta > 0 else "变小"
        return {"项目": item, "本版": cur_txt, "对比版": ref_txt,
                "差异": diff_txt, "结论": conclusion}
    same = str(_cell(cur)) == str(_cell(ref))
    return {"项目": item, "本版": cur_txt, "对比版": ref_txt,
            "差异": "无差异" if same else "内容不同",
            "结论": "一致" if same else "有变化"}


def _padstack_map(doc: dict) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for item in doc.get("via_padstacks") or []:
        name = str(item.get("name") or "")
        base = name.lower()
        for suffix in (".pad", ".dra", ".psm"):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
        out[base or name] = item
    return out


def _drill_size_map(doc: dict) -> Dict[Tuple[float, str], int]:
    out: Dict[Tuple[float, str], int] = {}
    for row in doc.get("drill_rows") or []:
        size = row.get("size_mm")
        if size is None:
            continue
        key = (round(float(size), 3), str(row.get("plating") or "PTH"))
        out[key] = out.get(key, 0) + int(row.get("qty") or 0)
    return out


def _build_difference_rows(cur_doc: dict, ref_doc: dict,
                           limit: int = 400) -> Tuple[List[dict], dict]:
    """逐项对比两份 extract_brd_design() 结果。返回 (rows, stats)。

    对比项: 层数/网络数/器件数/差分对数/最小线宽/最小线距/最小钻孔/孔环(机械与激光)/
    板厚/板尺寸/过孔焊盘栈清单(增删改)/约束集差异/钻孔孔径清单变化。
    """
    rows: List[dict] = []
    size_cur = cur_doc.get("board_size_mm") or []
    size_ref = ref_doc.get("board_size_mm") or []

    def pair(values: list, idx: int) -> Any:
        return values[idx] if isinstance(values, (list, tuple)) and len(values) > idx else None

    for item, key, unit, kind in (
        ("层数", "layer_count", " 层", "count"),
        ("网络数", "net_count", " 个", "count"),
        ("器件数", "part_count", " 个", "count"),
        ("差分对数", "diff_pair_count", " 对", "count"),
    ):
        rows.append(_diff_row(item, cur_doc.get(key), ref_doc.get(key), unit, kind))
    rows.append(_diff_row("最小线宽", cur_doc.get("min_line_width_mm"),
                          ref_doc.get("min_line_width_mm"), "mm", "looser_up"))
    rows.append(_diff_row("最小线距", cur_doc.get("min_spacing_mm"),
                          ref_doc.get("min_spacing_mm"), "mm", "looser_up"))
    rows.append(_diff_row("最小颈宽", cur_doc.get("min_neck_width_mm"),
                          ref_doc.get("min_neck_width_mm"), "mm", "looser_up"))
    rows.append(_diff_row("最小机械钻孔", cur_doc.get("min_drill_mm"),
                          ref_doc.get("min_drill_mm"), "mm", "plain"))
    rows.append(_diff_row("最小激光孔", cur_doc.get("min_laser_via_mm"),
                          ref_doc.get("min_laser_via_mm"), "mm", "plain"))
    rows.append(_diff_row("单边孔环 (机械孔)", cur_doc.get("min_annular_ring_mm"),
                          ref_doc.get("min_annular_ring_mm"), "mm", "looser_up"))
    rows.append(_diff_row("单边孔环 (激光/盲埋孔)", cur_doc.get("min_annular_ring_laser_mm"),
                          ref_doc.get("min_annular_ring_laser_mm"), "mm", "looser_up"))
    rows.append(_diff_row("板厚", cur_doc.get("board_thickness_mm"),
                          ref_doc.get("board_thickness_mm"), "mm", "plain"))
    rows.append(_diff_row("板宽", pair(size_cur, 0), pair(size_ref, 0), "mm", "plain"))
    rows.append(_diff_row("板高", pair(size_cur, 1), pair(size_ref, 1), "mm", "plain"))

    # ---- 过孔焊盘栈清单: 增 / 删 / 改 ----
    cur_pads, ref_pads = _padstack_map(cur_doc), _padstack_map(ref_doc)
    pad_added = pad_removed = pad_changed = pad_same = 0
    for name in sorted(set(cur_pads) | set(ref_pads)):
        cur_p, ref_p = cur_pads.get(name), ref_pads.get(name)
        display = str((cur_p or ref_p or {}).get("name") or name)
        if cur_p is None:
            pad_removed += 1
            rows.append({"项目": f"过孔焊盘栈 {display}", "本版": "无",
                         "对比版": display, "差异": "删除",
                         "结论": "只存在于对比版 (本版已删掉这个焊盘栈)"})
            continue
        if ref_p is None:
            pad_added += 1
            rows.append({"项目": f"过孔焊盘栈 {display}", "本版": display,
                         "对比版": "无", "差异": "新增",
                         "结论": "只存在于本版 (新加的焊盘栈, 需确认孔环/钻孔能力)"})
            continue
        changes = []
        for label, key, unit in (("钻孔", "drill_mm", "mm"), ("焊盘", "pad_mm", "mm"),
                                 ("单边孔环", "annular_ring_mm", "mm"),
                                 ("反焊盘", "antipad_mm", "mm")):
            cv, rv = _num(cur_p.get(key)), _num(ref_p.get(key))
            if cv is None and rv is None:
                continue
            if cv is None or rv is None or abs(cv - rv) > 1e-6:
                changes.append(f"{label} {_fmt_value(cur_p.get(key))} <- "
                               f"{_fmt_value(ref_p.get(key))}{unit}")
        if changes:
            pad_changed += 1
            rows.append({"项目": f"过孔焊盘栈 {display}",
                         "本版": display, "对比版": display,
                         "差异": "; ".join(changes),
                         "结论": "焊盘栈定义改变 (孔环/钻孔变化会影响制程判定)"})
        else:
            pad_same += 1

    # ---- 约束集差异 ----
    cset_rows = 0
    for group, key in (("Physical", "physical_csets"), ("Spacing", "spacing_csets")):
        cur_sets = cur_doc.get(key) or {}
        ref_sets = ref_doc.get(key) or {}
        for cset in sorted(set(cur_sets) | set(ref_sets)):
            cur_e, ref_e = cur_sets.get(cset), ref_sets.get(cset)
            if cur_e is None:
                cset_rows += 1
                rows.append({"项目": f"{group}/{cset}", "本版": "无",
                             "对比版": "有", "差异": "本版删除了该约束集",
                             "结论": "该约束集在本版不存在, 对应规则可能落到 DEFAULT"})
                continue
            if ref_e is None:
                cset_rows += 1
                rows.append({"项目": f"{group}/{cset}", "本版": "有",
                             "对比版": "无", "差异": "本版新增了该约束集",
                             "结论": "新增约束集, 需确认它被哪些网络引用"})
                continue
            for prop in sorted(set(cur_e) | set(ref_e)):
                cv, rv = cur_e.get(prop), ref_e.get(prop)
                if _cell(cv) == _cell(rv):
                    continue
                cset_rows += 1
                rows.append(_diff_row(f"{group}/{cset}/{prop}", cv, rv, "",
                                      "looser_up" if "MIN" in prop.upper() else "plain"))

    # ---- 钻孔孔径清单变化 ----
    cur_drills, ref_drills = _drill_size_map(cur_doc), _drill_size_map(ref_doc)
    drill_added = drill_removed = drill_qty_changed = drill_same = 0
    for size, plating in sorted(set(cur_drills) | set(ref_drills)):
        label = f"钻孔 Ø{size:g}mm ({plating})"
        cv, rv = cur_drills.get((size, plating)), ref_drills.get((size, plating))
        if cv is None:
            drill_removed += 1
            rows.append({"项目": label, "本版": "无", "对比版": f"{rv} 个",
                         "差异": "孔径消失", "结论": "对比版有这种孔径, 本版没有"})
        elif rv is None:
            drill_added += 1
            rows.append({"项目": label, "本版": f"{cv} 个", "对比版": "无",
                         "差异": "新增孔径", "结论": "本版新增这种孔径 (需确认钻头/能力)"})
        elif cv != rv:
            drill_qty_changed += 1
            rows.append({"项目": label, "本版": f"{cv} 个", "对比版": f"{rv} 个",
                         "差异": f"{cv - rv:+d} 个", "结论": "数量变化"})
        else:
            drill_same += 1

    truncated = len(rows) > limit
    stats = {
        "差异行数": len(rows), "截断": truncated,
        "焊盘栈新增": pad_added, "焊盘栈删除": pad_removed,
        "焊盘栈改变": pad_changed, "焊盘栈不变": pad_same,
        "约束差异": cset_rows,
        "钻孔孔径新增": drill_added, "钻孔孔径删除": drill_removed,
        "钻孔数量变化": drill_qty_changed, "钻孔孔径不变": drill_same,
    }
    return rows[:limit], stats


def _write_difference_csv(path: str, rows: List[dict]) -> str:
    return _write_rows_csv(path, ["项目", "本版", "对比版", "差异", "结论"], rows)


def cap_design_difference(ctx: Context) -> Result:
    cap = CATALOG_MAP["db.design_difference"]
    howto = ("把两个 .brd 一起传给工具（第二个 .brd 通过导出文件参数传入），例如 "
             "python main.py --analyze 新.brd --analyze-files 旧.brd")
    others, same = _diff_candidates(ctx)
    if not others:
        if same:
            res = unavailable(cap, f"对比板与当前板路径相同 ({same[0]}), 没有第二块板可比较",
                              howto, detail={"same_path": same[0]})
        else:
            res = unavailable(cap, "没有提供对比板 (需要第二个 .brd 文件)",
                              howto, detail={"export_files": list(ctx.export_files or [])})
        return res

    ref_path = others[0]
    try:
        from src.brd_design import extract_brd_design

        ref_doc = extract_brd_design(ref_path)
    except Exception as exc:  # noqa: BLE001  解析失败必须说清原因, 不能出空表
        return unavailable(cap, f"对比板解析失败: {exc}", howto,
                          detail={"compare_path": ref_path})
    if not ref_doc.get("ok"):
        error = str(ref_doc.get("error") or "未知原因")
        if ref_path not in error:
            error = f"{error} ({ref_path})"
        return unavailable(cap, f"对比板解析失败: {error}", howto,
                          detail={"compare_path": ref_path})
    if not ctx.design_ok:
        return unavailable(cap, f"当前板解析失败, 无法比较: {ctx.design_error or '未知原因'}",
                          howto, detail={"current_error": ctx.design_error})

    rows, stats = _build_difference_rows(ctx.design, ref_doc)
    db_dir = _db_dir(ctx)
    csv_path = _write_difference_csv(os.path.join(db_dir, "design_difference.csv"), rows)
    res = Result(cap, INFO, "")
    res.add_all(rows[:300], ["项目", "本版", "对比版", "差异", "结论"])
    res.artifact("design_difference_csv", csv_path,
                 "设计版本差异表 (项目/本版/对比版/差异/结论)")

    changed = [r for r in rows if r["差异"] not in ("无差异",)]
    res.metric("对比板", os.path.basename(ref_path), "", ref_path)
    res.metric("对比项", len(rows), "项", "两份 extract_brd_design() 结果逐项对比", "high")
    res.metric("有差异的项", len(changed), "项", "同上")
    res.metric("焊盘栈 新增/删除/改变", f"{stats['焊盘栈新增']}/{stats['焊盘栈删除']}/"
               f"{stats['焊盘栈改变']}", "个", "过孔焊盘栈清单比对")
    res.metric("钻孔孔径 新增/删除/数量变化",
               f"{stats['钻孔孔径新增']}/{stats['钻孔孔径删除']}/{stats['钻孔数量变化']}",
               "种", "钻孔表按孔径+镀覆聚合比对")
    res.metric("约束集有差异的属性", stats["约束差异"], "条",
               "PhysicalCSet/SpacingCSet 逐属性比对")
    res.summary = (f"本版 vs {os.path.basename(ref_path)}: {len(rows)} 项对比, "
                   f"其中 {len(changed)} 项有差异 "
                   f"(焊盘栈 +{stats['焊盘栈新增']}/-{stats['焊盘栈删除']}/"
                   f"改{stats['焊盘栈改变']}, 钻孔孔径 +{stats['钻孔孔径新增']}/"
                   f"-{stats['钻孔孔径删除']})")
    if stats["截断"]:
        res.warn("差异行数超过 300 行, 明细表只列前 300 行; 完整差异见 CSV")
    if len(others) > 1:
        res.warn(f"提供了 {len(others)} 块对比板, 本次只比较了第一块 ({os.path.basename(ref_path)}); "
                 f"其余: {'、'.join(os.path.basename(p) for p in others[1:])}")
    if not changed:
        res.warn("逐项对比没有发现差异 —— 请确认两块板确实是不同版本, "
                 "而不是同一块板的两个副本")
    res.warn("对比只覆盖能从 .brd 直读的项 (层数/网络/器件/约束/钻孔/焊盘栈/板框); "
             "布线几何与网络连接的差异需要 DRC 报告或网表比对")
    res.source(f"当前板: {ctx.brd_path}")
    res.source(f"对比板: {ref_path}")
    return res


# ==========================================================================
# 8.8 Report Export —— 自定义报告输出 (本类产物索引)
# ==========================================================================
_INDEX_COLUMNS = ["类别", "名称", "路径或来源", "大小KB", "行数", "说明"]
_DB_FILE_CAP: Dict[str, Tuple[str, str]] = {
    "netlist.csv": ("db.netlist", "网络清单"),
    "netlist.json": ("db.netlist", "{网络名: [引脚...]}"),
    "symbol_spreadsheet.csv": ("db.symbol_spreadsheet", "器件符号表"),
    "placement.csv": ("db.placement", "元件布局坐标"),
    "placement.txt": ("db.placement", "Allegro 风格布局文本"),
    "design_parameters.csv": ("db.parameters", "设计参数"),
    "design_parameters.json": ("db.parameters", "设计参数 + 出处"),
    "constraint_table.csv": ("db.parameters", "完整约束表"),
    "annotation.csv": ("db.annotation", "文字/标注图形"),
    "constraints.tcf": ("db.techfile", "可读约束清单"),
    "design_difference.csv": ("db.design_difference", "设计版本差异表"),
    "export_index.csv": ("db.report_export", "本索引"),
    "export_index.json": ("db.report_export", "本索引 (结构化)"),
}


def _count_lines(path: str) -> Any:
    try:
        with open(path, "rb") as f:
            data = f.read()
        return data.count(b"\n") + (0 if data.endswith(b"\n") or not data else 1)
    except OSError as exc:
        return f"读取失败: {exc}"


def _scan_db_files(db_dir: str) -> List[dict]:
    """扫描 <out>/database/ 里实际存在的文件 (不猜、不列没生成的东西)。"""
    out: List[dict] = []
    try:
        names = sorted(os.listdir(db_dir))
    except OSError:
        return out
    for name in names:
        path = os.path.join(db_dir, name)
        if not os.path.isfile(path):
            continue
        try:
            size_kb = round(os.path.getsize(path) / 1024.0, 2)
        except OSError:
            size_kb = ""
        out.append({"名称": name, "绝对路径": path, "大小KB": size_kb,
                    "行数": _count_lines(path)})
    return out


def _readiness_rows(ctx: Context) -> List[dict]:
    """数据准备度: 哪些数据有、哪些缺 —— 缺的写清怎么补。"""
    rows: List[dict] = []

    def add(name: str, ok: bool, detail: str, howto: str = "") -> None:
        rows.append({
            "类别": "数据准备度", "名称": name, "路径或来源": detail,
            "大小KB": "", "行数": "",
            "说明": "有" if ok else ("缺 — " + howto if howto else "缺"),
        })

    nets = facts.net_names(ctx)
    add("网络名 (Net.xml)", bool(nets), "objects/Net.xml",
        "如果没有该归档对象, 网络名不可得; Allegro: Tools > Reports > Netlist")
    pins = _net_pin_map(ctx)
    add("网络引脚连接 (网表)", bool(pins), "导入的 pstxnet.dat / NET: 文本",
        "Allegro: File > Export > Netlist (pstxnet.dat), 用 --analyze-files 传给工具")
    comp = facts.components(ctx)
    add("器件位号 (Part.xml)", bool(comp.get("ok")),
        comp.get("source") or comp.get("reason", "objects/Part.xml"),
        "确认是真实 PCB 设计; 或提供 BOM")
    pkg = _package_map(ctx.objects, comp.get("refdes") or []) if comp.get("ok") else \
        {"by_refdes": {}}
    add("器件封装/料号 (PartDefn/Gate)", bool(pkg.get("by_refdes")),
        "objects/PartDefn.xml / objects/Gate.xml",
        "提供含封装字段的 .brd 或 ERP/BOM 导出")
    add("器件 XY 坐标 (Placement)", bool(ctx.has_components_xy),
        f"{len(ctx.exports.get('components') or [])} 条坐标", ctx.howto_components_xy())
    add("孔位坐标 (NC Drill)", bool(ctx.has_holes_xy),
        f"{len(ctx.exports.get('holes') or [])} 条孔位", ctx.howto_holes_xy())
    physical = ctx.design.get("physical_csets") or {}
    spacing = ctx.design.get("spacing_csets") or {}
    add("物理约束 (PhysicalCSet.xml)", bool(physical), f"{len(physical)} 个约束集",
        "确认 .brd 是 17.x 及以上版本")
    add("间距约束 (SpacingCSet.xml)", bool(spacing), f"{len(spacing)} 个约束集",
        "确认 .brd 是 17.x 及以上版本")
    add("内嵌设计快照 (几何)", bool(ctx.has_snapshot),
        f"{len(ctx.raw_entries) if ctx.has_snapshot else 0} 条图形",
        "确认 .brd 内含设计快照 (design.comps)")
    add("坐标标定 (mm 换算)", bool(ctx.has_scale),
        f"{ctx.scale_mm_per_unit} ({ctx.outline.get('method') or '未知方法'})",
        "确认 .brd 的 Draw Size 可读")
    others, _same = _diff_candidates(ctx)
    add("对比板 (第二个 .brd)", bool(others),
        others[0] if others else "未提供",
        "python main.py --analyze 新.brd --analyze-files 旧.brd")
    return rows


def _build_index_rows(db_dir: str, ctx: Context) -> Tuple[List[dict], int]:
    """产物文件行 + 数据准备度行; 返回 (rows, 非索引产物文件数)。"""
    rows: List[dict] = []
    produced = 0
    for rec in _scan_db_files(db_dir):
        cap_id, note = _DB_FILE_CAP.get(rec["名称"], ("", "database 类产物"))
        rows.append({"类别": "产物文件", "名称": rec["名称"], "路径或来源": rec["绝对路径"],
                     "大小KB": rec["大小KB"], "行数": rec["行数"],
                     "说明": (f"{cap_id} · {note}" if cap_id else note)})
        if not rec["名称"].startswith("export_index."):
            produced += 1
    rows.extend(_readiness_rows(ctx))
    return rows, produced


def cap_report_export(ctx: Context) -> Result:
    cap = CATALOG_MAP["db.report_export"]
    db_dir = _db_dir(ctx)
    csv_path = os.path.join(db_dir, "export_index.csv")
    json_path = os.path.join(db_dir, "export_index.json")
    res = Result(cap, PASS, "")

    rows, produced = _build_index_rows(db_dir, ctx)

    def payload(all_rows: List[dict]) -> dict:
        return {
            "board": ctx.board_name,
            "brd_path": ctx.brd_path,
            "database_dir": db_dir,
            "generated_at": _now(),
            "note": ("本文件只是「数据库与设计数据导出」这一类的产物索引; "
                     "全量分析报告 (Excel/HTML/TXT/JSON/CSV) 由主流程 writers.py 生成, "
                     "这里不重复实现。"),
            "files": [r for r in all_rows if r["类别"] == "产物文件"],
            "readiness": [r for r in all_rows if r["类别"] == "数据准备度"],
        }

    # 先写一次 (此时索引还没进目录), 再重扫一次把自己也算进去
    _write_rows_csv(csv_path, _INDEX_COLUMNS, rows)
    _write_json_file(json_path, payload(rows))
    rows, produced = _build_index_rows(db_dir, ctx)
    _write_rows_csv(csv_path, _INDEX_COLUMNS, rows)
    _write_json_file(json_path, payload(rows))

    res.artifact("export_index_csv", csv_path,
                 "本类产物索引 + 数据准备度 (类别/名称/路径或来源/大小KB/行数/说明)")
    res.artifact("export_index_json", json_path, "索引与准备度的结构化版本")

    file_rows = [r for r in rows if r["类别"] == "产物文件"]
    ready = [r for r in rows if r["类别"] == "数据准备度"]
    have = [r for r in ready if str(r["说明"]).startswith("有")]
    missing = [r for r in ready if not str(r["说明"]).startswith("有")]
    res.extra["files"] = file_rows
    res.extra["readiness"] = ready
    res.add_all(rows[:120], _INDEX_COLUMNS)

    res.metric("本类产物文件", len(file_rows), "个", db_dir)
    res.metric("其中非索引文件", produced, "个", "本次 database 类各项能力生成")
    res.metric("数据准备度", f"{len(have)}/{len(ready)}", "项",
               "按 .brd 归档对象 + 导入的导出文件逐项判定")
    if file_rows:
        res.metric("产物总大小", round(sum(float(r["大小KB"]) for r in file_rows
                                          if isinstance(r["大小KB"], (int, float))), 2),
                   "KB", "实际文件大小")
    if produced:
        res.summary = (f"database 类共生成 {len(file_rows)} 个文件 (含本索引), "
                       f"数据准备度 {len(have)}/{len(ready)} 项有数据; "
                       f"索引 CSV 用「类别」列区分「产物文件」与「数据准备度」")
    else:
        res.status = WARN
        res.summary = (f"database 目录里只有本索引, 本次没有其它产物文件; "
                       f"数据准备度 {len(have)}/{len(ready)} 项有数据")
        res.need("先运行 database 类的数据导出能力 (netlist / symbol_spreadsheet / "
                 "placement / parameters / annotation / techfile / design_difference)",
                 "命令行用 --analyze-only db.netlist 这类 id 单项运行, 或整类运行 "
                 "--analyze-category database")
    for row in missing[:6]:
        res.warn(f"缺数据: {row['名称']} — {row['说明']}")
    res.warn("本项不重复实现全量分析报告: 那份报告 (Excel/HTML/TXT/JSON/CSV) 由主流程的 "
             "writers.py 生成, 本项只汇总「数据库与设计数据导出」这一类的产物")
    res.source(f"扫描目录: {db_dir}")
    res.source("数据准备度判定来源: .brd 归档对象 + 导入的导出文件 + Context 缓存")
    return res


# ==========================================================================
# 能力注册表 (第 8 类: 数据库与设计数据导出)
# ==========================================================================
CATALOG: List[Capability] = [
    Capability("db.netlist", "database", "Netlist Export", "网络表导出",
               "导出网络清单 (网络名/差分对/电源地分类) 与网络到引脚的表; "
               "引脚需要 Allegro 导出的网表",
               cap_netlist, "brd",
               ["objects/Net.xml", "导入的网表文件 (pstxnet.dat)"],
               ["netlist.csv", "netlist.json"]),
    Capability("db.symbol_spreadsheet", "database", "Symbol Spreadsheet", "器件符号信息",
               "导出器件位号/前缀/类别/封装; 封装需要 PartDefn 或 ERP 数据",
               cap_symbol_spreadsheet, "brd",
               ["objects/Part.xml", "objects/PartDefn.xml / objects/Gate.xml"],
               ["symbol_spreadsheet.csv"]),
    Capability("db.placement", "database", "Placement Export", "元件布局数据",
               "导出器件 XY/旋转/面别 (CSV + Allegro 风格 TXT)",
               cap_placement, "export",
               ["Placement/SKILL 坐标文件"], ["placement.csv", "placement.txt"]),
    Capability("db.parameters", "database", "Parameters Export", "设计参数导出",
               "导出设计参数 (字段名与 DFM 口径一致) 与完整约束表",
               cap_parameters, "brd",
               ["PhysicalCSet.xml", "SpacingCSet.xml", "钻孔表", "Part.xml"],
               ["design_parameters.json", "design_parameters.csv", "constraint_table.csv"]),
    Capability("db.annotation", "database", "Annotation Export", "标注信息导出",
               "导出快照里的文字/标注图形与位置; 快照没有位号与文字的绑定关系",
               cap_annotation, "snapshot",
               ["内嵌设计快照 TEXT/DIMENSION/REFDES/TITLE 图形"],
               ["annotation.csv"]),
    Capability("db.techfile", "database", "Techfile Export", "约束技术文件导出",
               "把物理/间距约束导出成可读的 constraints.tcf; "
               "不是 Allegro 可 import 的 techfile",
               cap_techfile, "brd",
               ["objects/PhysicalCSet.xml", "objects/SpacingCSet.xml"],
               ["constraints.tcf"]),
    Capability("db.design_difference", "database", "Design Difference", "设计版本差异比较",
               "两块 .brd 逐项对比: 层数/网络/器件/约束/钻孔/焊盘栈/板框",
               cap_design_difference, "brd",
               ["本版 .brd", "对比板 .brd (--analyze-files)"],
               ["design_difference.csv"]),
    Capability("db.report_export", "database", "Report Export", "自定义报告输出",
               "汇总本类产物的索引与数据准备度; 全量报告由主流程 writers.py 生成",
               cap_report_export, "brd",
               ["<out>/database/ 目录", "Context 数据准备度"],
               ["export_index.csv", "export_index.json"]),
]
CATALOG_MAP: Dict[str, Capability] = {c.id: c for c in CATALOG}
