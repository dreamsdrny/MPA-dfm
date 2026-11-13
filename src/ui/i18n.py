"""Runtime Chinese/English UI; underlying field keys, paths and results stay intact."""
import re
import tkinter as tk
from tkinter import ttk

LANGUAGE = "zh"
TRANSLATIONS = {
    "执行 PCB 分析":"Run PCB analysis", "打开 BRD":"Open BRD", "关闭":"Close",
    "网络定位":"Net location", "报告导出":"Export reports", "DFM 综合报告":"DFM report",
    "PCB 报告":"PCB report", "SMT 报告":"SMT report", "报告隐藏低优先级":"Hide low-priority report items",
    "需要独立 PCB 报告":"Enable separate PCB report", "执行分析时后台导出 Gerber":"Export Gerber in background on analysis",
    "选择已有 Gerber":"Select Gerber folder", "导出 Gerber":"Export Gerber", "Gerber 问题预览":"Gerber issue preview",
    "问题坐标来源":"Coordinate source", "选择导出目录":"Select export folder", "导入 COMP_XY":"Import COMP_XY",
    "生成 Allegro 坐标脚本":"Generate coordinate scripts", "设置 PCB Editor":"Set PCB Editor",
    "打开 BRD 并提取坐标":"Open BRD + export coordinates", "选点同步到 Allegro":"Sync selection to Allegro",
    "并排显示":"Side by side", "PCB 厂 · 制造能力分析":"PCB manufacturing capabilities",
    "SMT 厂 · 工艺能力分析 / YAML":"SMT capabilities / YAML", "设计参数":"Design inputs",
    "分析结果":"Analysis results", "BRD 值自动带入并隐藏 · 只留需人工补的项":"BRD inputs auto-loaded; fill missing data",
    "BRD 文件":"BRD file", "浏览":"Browse", "从 BRD 读取":"Read BRD", "板厂":"PCB fabricator",
    "板类型":"Board type", "能力等级":"Capability level", "铜厚 (oz)":"Copper (oz)", "表面处理":"Surface finish",
    "单板名":"Board name", "极性已标注":"Polarity marked", "已拼板":"Panelized",
    "显示 BRD 带入项":"Show BRD inputs", "确认全部示例值":"Confirm all sample inputs", "手工":"Manual",
    "示例":"Sample", "待填":"Missing", "坐标定位":"Coordinate sources", "制程能力比对":"Capability comparison",
    "问题 X/Y 坐标":"Issue X/Y coordinates", "BRD 器件 X/Y":"BRD component X/Y", "结果":"Result",
    "来源":"Source", "类别":"Category", "详情":"Details", "检查项":"Check", "坐标单位":"Coordinate unit",
    "网络/焊盘":"Net / padstack", "丝印/对象层":"Object layer", "定位状态/说明":"Location / notes",
    "打开 BRD 定位":"Locate in BRD", "上一个点":"Previous point", "下一个点":"Next point",
    "复制坐标命令":"Copy coordinate command", "位号筛选：":"Reference filter:", "定位器件":"Locate component",
    "复制 X/Y":"Copy X/Y", "位号":"Reference", "BRD单位":"BRD unit", "面":"Side", "旋转":"Rotation",
    "尚未执行分析":"Analysis not run", "输入已变化，请重新执行 PCB 分析":"Inputs changed; run PCB analysis again",
    "输入已变化，请重新执行 SMT 分析":"Inputs changed; run SMT analysis again",
    "双击有坐标的问题点，在 Allegro 打开 BRD 并缩放到该点。":"Double-click an issue to locate it in Allegro.",
    "双击器件可在 Allegro 定位":"Double-click a component to locate it in Allegro",
    "尚未提取器件坐标；点「打开 BRD」自动提取":"No component coordinates. Open BRD to export them.",
    "未读到器件放置原点；请点「打开 BRD」提取":"No component origins found. Open BRD to export them.",
    "PCB 制造能力 · SMT 工艺能力 · BRD 问题定位":"PCB / SMT capabilities and BRD cross-probe",
    "导入 SMT YAML":"Import SMT YAML", "执行 SMT 分析":"Run SMT analysis", "状态":"Status", "输入":"Input",
    "能力":"Capability", "单位":"Unit", "裕量":"Margin", "导入 SMT 厂商 YAML，选择产线/等级后执行能力比较":"Import SMT YAML, select line/profile, then analyze",
    "设计数据共用 PCB 页签；工艺实测值在本页明确输入":"PCB design inputs are shared; enter process measurements here",
    "输入值（空白时引用已确认的 PCB 页签字段）":"Inputs (blank uses confirmed PCB fields)",
    "就绪":"Ready", "PCB 分析完成":"PCB analysis complete", "PCB 分析失败":"PCB analysis failed",
    "就绪：导入 BRD 后点「执行分析」":"Ready: import BRD, then run analysis",
    "正在后台执行 PCB 分析，界面可继续操作…":"PCB / Gerber analysis running in background…",
    "Allegro：未连接":"Allegro: disconnected", "导出完成":"Export complete", "导出失败":"Export failed",
    "参数错误":"Invalid inputs", "提示":"Information", "报告":"Report", "Gerber 导出失败":"Gerber export failed",
    "层压":"Lamination", "压合":"Lamination", "线路":"Traces", "钻孔":"Drilling", "塞孔":"Via plugging",
    "阻焊":"Solder mask", "丝印":"Silkscreen", "外形":"Outline", "板材":"Material", "电镀":"Plating",
    "拼板":"Panelization", "组装":"Assembly", "装配":"Assembly", "覆盖膜":"Coverlay", "测试点":"Test points",
    "导入":"Import", "缺失/解析":"Missing / parsing", "图形来源":"Film sources", "丝印位置":"Silkscreen location",
    "没有可判定的参数":"No confirmed inputs", "请先选择 BRD":"Select a BRD file first",
    "请先完成当前输入的 PCB 分析":"Complete PCB analysis for current inputs first",
    "请先完成当前输入的 SMT 分析":"Complete SMT analysis for current inputs first",
    "请先完成 Gerber 分析，再打开问题预览":"Complete Gerber analysis before opening the preview",
    "深色":"Dark", "浅色":"Light", "语言":"Language", "主题":"Theme",
    "极性标识 / 拼板：BRD 里读不到，请按实际板子勾选；没勾选会作为高等级问题写进结果":"Polarity/panelization require confirmation; unchecked flags produce high-priority issues",
    "不符合项 -> 数量 + 元件/孔位坐标":"Issues -> count and component/hole coordinates",
    "器件放置原点，保留数据库原点":"Component placement origin; database origin preserved",
    "PCB 分析期间输入已变化，已丢弃过期结果，请重新分析":"Inputs changed while analyzing; stale results discarded",
    "需确认":"Review", "未提供":"Not provided", "缺失":"Missing", "未配置":"Unconfigured",
    "选择同一版本的一套 Gerber 目录":"Select one Gerber set for the same board revision",
    "坐标原点":"Coordinate origin", "该 Gerber 未确认与 BRD 原点一致，请使用 Gerber 问题预览查看位置。":"Gerber origin is not confirmed against BRD. Use Gerber issue preview.",
    "导出":"Export", "工艺实测":"Process measurement", "设计/已确认":"Confirmed design",
}


def translate(text):
    if LANGUAGE != "en" or not isinstance(text, str):
        return text
    if text in TRANSLATIONS:
        return TRANSLATIONS[text]
    # A stable field ID is used for technical inputs without guessing domain wording.
    from src.dfm_inputs import DFM_FIELDS
    for _category, key, label, _default in DFM_FIELDS:
        if text == label:
            unit = re.search(r"\([^)]*\)", label)
            return key.replace("_", " ").capitalize() + (" " + unit.group() if unit else "")
    match = re.fullmatch(r"BRD 器件 X/Y \((\d+)\)", text)
    if match:
        return "BRD component X/Y (" + match[1] + ")"
    # Translate known fragments in dynamic statuses; preserve numeric values/paths.
    replacements = dict(TRANSLATIONS, 正在读取="Reading", 已复制="Copied", 已导入="Imported",
        已提取="Exported", 个器件=" components", 器件原点="Component origins", 当前显示="Shown",
        后台处理中="Background tasks", 处理中="Working", 已连接="connected", 未连接="disconnected",
        数据不足="Missing data", 不通过="FAIL", 通过="PASS", 不符合="Non-compliant", 待复核="Review",
        缺数据="Missing inputs", 待补几何="Geometry needed", 可定位="Locatable", 按来源核对="verify source",
        原始分析结论="Original verdict", 需复核="Needs review", 最小="Minimum", 最大="Maximum",
        输入已变化="Inputs changed", 丝印="Silkscreen", 坐标="coordinates", 已定位选中问题点="selected point located")
    for source in sorted(replacements, key=len, reverse=True):
        text = text.replace(source, replacements[source])
    return text


def localize_widgets(root):
    """Translate widget labels, notebook tabs and headings without rewriting Tk inputs."""
    def walk(widget):
        try:
            if "text" in widget.keys():
                current = str(widget.cget("text"))
                previous = getattr(widget, "_i18n_last", None)
                if current != previous:
                    widget._i18n_source = current
                source = getattr(widget, "_i18n_source", current)
                rendered = translate(source)
                widget.configure(text=rendered)
                widget._i18n_last = rendered
            if isinstance(widget, (ttk.Label, tk.Label)) and widget.cget("textvariable"):
                original = getattr(widget, "_i18n_original_var", None)
                if original is None:
                    original = str(widget.cget("textvariable"))
                    widget._i18n_original_var = original
                    mirror = tk.StringVar(master=widget)
                    widget._i18n_mirror = mirror
                    def update(*_, label=widget, var=original, output=mirror):
                        try:
                            output.set(translate(label.getvar(var)))
                        except tk.TclError:
                            pass
                    command = widget.register(update)
                    widget.tk.call("trace", "add", "variable", original, "write", command)
                    def release(event, label=widget, var=original, callback=command):
                        if event.widget is label:
                            try:
                                label.tk.call("trace", "remove", "variable", var, "write", callback)
                            except tk.TclError:
                                pass
                    widget.bind("<Destroy>", release, add="+")
                    widget.configure(textvariable=mirror)
                widget._i18n_mirror.set(translate(widget.getvar(original)))
            if isinstance(widget, ttk.Notebook):
                for tab in widget.tabs():
                    text = widget.tab(tab, "text")
                    sources = getattr(widget, "_i18n_tabs", {})
                    old, last = sources.get(tab, (text, None))
                    if text != last:
                        old = text
                    new = translate(old)
                    sources[tab] = (old, new)
                    widget._i18n_tabs = sources
                    widget.tab(tab, text=new)
            if isinstance(widget, ttk.Treeview):
                for col in widget.cget("columns"):
                    text = widget.heading(col, "text")
                    sources = getattr(widget, "_i18n_headings", {})
                    old, last = sources.get(col, (text, None))
                    old = text if text != last else old
                    new = translate(old)
                    sources[col] = (old, new)
                    widget._i18n_headings = sources
                    widget.heading(col, text=new)
        except tk.TclError:
            return
        for child in widget.winfo_children():
            walk(child)
    walk(root)
