"""BRD 分析与输出引擎 (8 大类 / 40+ 项能力)。

用法
----
    from src.brd_analysis import analyze, capability_rows, write_reports

    bundle = analyze("board.brd", out_dir="output",
                     export_files=["drc.rpt", "place_txt.txt"])
    files = write_reports(bundle, "output", base_name="BRD分析报告")

设计要点 (与项目其它部分一致)
--------------------------
1. **能力注册表**: 能力定义集中在 catalog, 报告/命令行/界面都从它取列表;
2. **缺数据 ≠ 通过**: 数据不足的能力状态是 `unavailable`, 并写明缺什么、怎么补;
3. **可追溯**: 每条指标带 source/confidence;
4. **产物落地**: 能真生成的文件 (Gerber/DXF/报告/参数/CSV) 直接写盘, 并在
   bundle["artifacts"] 里登记; 生成不了的给出 Allegro 导出命令。

用户反馈里的功能分类, 对应到本包的 8 个模块:

    1 设计规则检查 (DRC)            cat_drc
    2 PCB 物理结构分析              cat_physical
    3 信号完整性与电气分析          cat_electrical
    4 制造文件输出                  cat_manufacturing
    5 PCB 报告输出                  cat_reports
    6 SMT 贴装与装配分析            cat_assembly
    7 机械结构与 ECAD/MCAD 数据交换  cat_mcad
    8 数据库与设计数据导出          cat_database
"""
from typing import Dict, Iterable, List, Optional

# 导入上下文相关模块：包含 Context 类定义及上下文加载函数
from src.brd_analysis.context import Context, load as load_context
# 导入分析执行模块：包含能力行生成函数及核心运行函数
from src.brd_analysis.runner import capability_rows, run, run_all
# 导入结果写入模块：包含多种格式（CSV, Excel, HTML, Text）的报告写入函数
from src.brd_analysis.writers import write_all, write_csv, write_excel, write_html, write_text

# 定义模块公开接口，限制 import * 时导出的内容

__all__ = [
    "Context", "load_context", "analyze", "capability_rows", "run",
    "write_reports", "write_all", "write_excel", "write_text", "write_html",
    "write_csv",
]


# 定义高层分析入口函数，接收文件路径及各类过滤配置，返回包含分析结果的数据包
def analyze(brd_path: str, out_dir: str = "",
            export_files: Optional[Iterable[str]] = None,
            only: Optional[Iterable[str]] = None,
            categories: Optional[Iterable[str]] = None,
            skip: Optional[Iterable[str]] = None,
            log=None) -> dict:
    """跑一轮完整分析, 返回 bundle (结果 + 统计 + 产物清单)。"""
    # 直接调用底层 run_all 引擎执行分析任务，并将所有接收到的参数原样透传
    return run_all(brd_path, out_dir=out_dir, export_files=export_files,
                   only=only, categories=categories, skip=skip, log=log)


def write_reports(bundle: dict, out_dir: str, base_name: str = "BRD分析报告",
                  formats: Optional[List[str]] = None) -> Dict[str, str]:
    """把 bundle 写成报告, 返回 {格式: 路径}。"""
    import os
    import pathlib

    # 如果调用方没有显式指定 base_name，则根据 bundle 中的 brd_path 自动生成
    if base_name == "BRD分析报告":
        brd_path = bundle.get("brd_path") or bundle.get("input_path") or bundle.get("board", {}).get("brd_path")
        if brd_path:
            stem = pathlib.Path(brd_path).stem  # 例如 "576"
            base_name = f"{stem}.dfm报告"

    # 确保输出目录存在，若不存在则递归创建，已存在时不报错
    os.makedirs(out_dir, exist_ok=True)
    return write_all(bundle, out_dir, base_name=base_name, formats=formats)
