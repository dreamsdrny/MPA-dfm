# MPA-DFM 代码审查与变更文档

- 审查日期：2026-11-11
- 代码基线：`2ac28cd`（Merge branch 'main'）+ 审查时未提交改动
- 审查范围：`main.py`、`evan_*.py`、`gui.py`、`src/**/*.py`、`tools/**/*.py`，共 88 个 Python 文件、约 31,300 行
- 审查方式：逐模块、逐函数阅读；配合 `ruff`（F/E9/B/PL 规则）、`compileall`、现有 27 个测试，并对可疑点用最小脚本复现
- 行号说明：均以审查时基线为准；本轮修复后同一文件的行号可能小幅偏移
- 结论分级：
  - **P0 阻断**：必然抛异常、崩溃或产出错误结论
  - **P1 严重**：特定输入下崩溃/静默错误/结论失真
  - **P2 一般**：边界条件、误报/漏报、单位或路径问题
  - **P3 建议**：可维护性、死代码、文案/性能问题

---

## 1. 基线验证

| 检查 | 命令 | 审查前 | 修复后 |
|---|---|---|---|
| 语法编译 | `python -m compileall -q src main.py` | 通过 | 通过 |
| 现有测试 | `python -m pytest tools -q` | 27 passed | 27 passed |
| 静态检查 | `ruff check src main.py --select F821,F811,F841` | 1 个 F821 + 2 个 F841 | 2 个 F841（仅死变量） |
| CLI 冒烟 | `python main.py --dfm template` | `UnboundLocalError: json` | 正常输出模板 |
| CLI 冒烟 | `python main.py --fill-checklist demo --output <temp>` | 可运行 | 可运行 |
| CLI 冒烟 | `python main.py --dfm dfm_design_sample.json --output <temp>` | 崩溃 | 正常出报告 |

本次只修改「可复现、影响明确」的问题；架构级/需要业务口径确认的问题只记录，不大改。所有修改均已跑 `pytest` 与 `compileall`。

---

## 2. 本轮已修复（FIX）

| ID | 原级别 | 位置 | 问题与触发条件 | 修复 | 验证 |
|---|---|---|---|---|---|
| FIX-001 | P0 | `src/brd_analysis/cat_manufacturing.py` 顶部 import / 原 716 行 | 代码写 `res.status = FAIL`，但模块只导入了 `INFO/PASS/WARN`。只要导入孔坐标且任一孔落在板框 bbox 外就抛 `NameError`，整个制造能力结果丢失 | 导入 `FAIL` | 编译 + ruff 无 F821 |
| FIX-002 | P0 | `main.py` `main()` 原 406 行 | 函数体内 `if args.input_project: import json` 使 `json` 成为整个函数的局部名；`--dfm`、`--nets` 分支在使用前未赋值，抛 `UnboundLocalError`，两条 CLI 功能完全不可用 | 删除函数内重复 `import json`，统一用模块级导入 | `--dfm template`、`--dfm 样例` 实跑通过 |
| FIX-003 | P1 | `src/brd_raw.py` `_UNIT_RE` | 正则分支顺序为 `MILS|MIL|MILLIMETERS|...`，`MILLIMETERS` 会先命中 `MIL`。mm 单位板被当成 mil，钻孔表尺寸整体乘 0.0254，`min_drill`/激光孔等丢失或错误 | 改为最长优先并加 `\b` 边界 | 7 种单位声明逐一验证：mm=1.0、mil=0.0254、inch=25.4 |
| FIX-004 | P1 | `src/dfm_engine.py` `analyze_design()` 器件段；`src/dfm_report.py` | 旧格式 BRD 回退 `pcb_reader` 时 `_components` 是 list；`analyze_design` 直接 `comps.get("ok")`，抛 `AttributeError`，DFM 分析中断；报告生成同样受影响 | 在 analyze_design 与 dfm_report 中统一把 list 归一化为 `{ok,count,refdes}` | `analyze_design({"_components":[{"ref":"R1"}]})` 不再崩溃 |
| FIX-005 | P1 | `src/checklist_auto_rules.py` `evaluate()` 的 `judge.kind == "dfm"` | DFM 项状态为 `info`（缺数据/未判定）时落入 else 返回 **FAIL**。例如「板厚」无能力范围时报告写“需与板厂确认”，Checklist 却判“板厚超差” | `info` 改为返回 `MANUAL`（缺数据待人工），`pass/warn_limit/fail` 逻辑不变 | 用 info 项构造规则，返回 MANUAL |
| FIX-006 | P1 | `src/checklist_pipeline.py` `run()` | `to_design_params()` 已把 `copper_um` 固定为 35，随后 `setdefault("copper_um", 用户值)` 永远不生效；UI/CLI 选 70um 仍按 35um 判定 | 改为直接赋用户值；`copper_oz` 同步覆盖 | 读取 demo 设计后覆盖为 70，确认生效 |
| FIX-007 | P2 | `main.py` `--fill-checklist` 分支 | 未传 `--copper-oz` 时硬编码 `1.0`，与“默认按 `--copper` 推算”矛盾；70um 板按 1oz 判阻焊桥 | 传 `None`，由 pipeline 按铜厚推算 | 代码路径检查 + 现有测试 |
| FIX-008 | P1 | `src/design_geometry.py` `board_outline()` | Draw Size 跨度一律按 mil `×0.0254` 标定，忽略 QuickView 的 `unit`。mm/inch 设计单位板整体尺寸、焊盘/丝印/KEEPIN 比例失真，却仍标 `confidence=high` | 按 `mil/mm/inch` 选择换算因子 | demo（mil）仍为 101.6×80.01mm；单位分支逐一验证 |
| FIX-009 | P1 | `src/pick.py` `write_report()` / `_write_scr()` | `locate.scr` 的 `zoom center` 单位取“导出文件单位”而不是 `.brd` 设计单位。demo 板 mil、导出 mm 时会写出 mm 数值，跳转坐标错 39.37 倍 | `analyze_and_locate` 把 `extracted["units"]` 传入；`_write_scr` 同时支持 inch | demo 路径检查；`_write_scr` 分支验证 |
| FIX-010 | P1 | `src/brd_analysis/cat_physical.py` `cap_placement` | `float(rot)` 未容错，Placement/SKILL CSV 的 `ROTATION` 为空串时抛 `ValueError`，整个 placement 能力失败 | 空/非数值归入“未知角度” | 语法/现有测试；空串路径人工验证 |
| FIX-011 | P1 | `src/brd_analysis/cat_reports.py` `cap_unconnected_report` | `native_hits==0` 且存在 DRC 报告时无条件 PASS，即使合并后的 `rows` 含有用户导入的未连接记录，形成假 PASS | 区分“合并后确实为 0”与“合并后有记录”；后者返回 WARN | 代码路径检查 |
| FIX-012 | P1 | `src/dfm_report.py` | 报告单元格未清洗：`\x00-\x1f` 令 openpyxl 抛 `IllegalCharacterError` 整份报告失败；以 `=` 开头的文本被写成公式（公式注入） | 递归清洗 analysis/board_meta，写入前统一去控制字符、截断 32000、字符串强制 `data_type="s"` | 构造 `BAD\x00NAME` 与 `=1+1`，均可生成且为文本 |
| FIX-013 | P1 | `src/ui/main_window.py` `_run_checklist` / `_execute_checklist` | 后台线程直接 `StringVar.get()` 读取 9 个 Tk 变量；主线程忙时阻塞，主循环未运行时抛 `RuntimeError: main thread is not in main loop` | 主线程先快照为普通 dict，再传工作线程 | 代码路径检查；现有 UI 自测 |
| FIX-014 | P3 | `src/ui/main_window.py` `_run_demo` | 演示运行前把 BRD/Checklist 路径置空，关闭时写回偏好，下次启动丢失上次选择 | 演示运行不再清空界面变量 | 代码路径检查 |
| FIX-015 | P3 | `src/ui/net_locate_dialog.py` 末尾 | `return dialog` 重复且不可达 | 删除重复行 | 编译 |
| FIX-016 | P2 | `src/checklist_template.py` `load_config` | `header_scan_rows` 配成非数字时 `int()` 抛异常；`template.columns` 配成非字典时 `.items()` 抛异常，与“配置写错不能让界面崩”的注释矛盾 | 增加类型/数值兜底 | 构造坏配置验证 |
| FIX-017 | P2 | `src/checklist_reader.py` `merge_checklist_with_defaults` | `dict(default_rules)` 是浅拷贝，合并时直接改默认规则里的嵌套 dict，跨调用污染默认值 | 改 `deepcopy` | 现有测试 |
| FIX-018 | P2 | `src/checklist_reader.py` `read_checklist_excel` | `int(no_val)` 遇到 Excel 常见字符串 `"1.0"` 抛 `ValueError`，整行被跳过 | `int(float(no_val))` | 现有测试 |
| FIX-019 | P3 | `src/report_generator.py` | `os.makedirs(os.path.dirname(output_path))` 在只给文件名时 `dirname` 为空，`makedirs("")` 抛错 | 统一 `abspath` 后取 dirname | 编译 |
| FIX-020 | P3 | `src/design_geometry.py` `_load_snapshot_cached` | 固定取 zip 第一个成员当 JSON；归档顺序变化时直接返回 None，所有几何能力被误判“无快照” | 优先找名字含 `design` 的 `.json`，找不到再回退首个成员 | demo 快照仍正常读取 |

---

## 3. 待修复 / 需业务确认（按优先级）

> 以下问题未在本轮修改，原因是要么需要业务口径确认，要么改动面较大、需要真实板/真实导出文件回归。

### P0/P1

| ID | 位置 | 问题 | 触发条件 | 影响 | 建议 |
|---|---|---|---|---|---|
| OPEN-001 | `src/brd_analysis/imports.py:217` | `parse_drc_report` 用 `ok = bool(items)`；合法但 0 违规的 DRC 报告被判“解析失败” | 板厂导出“无违规”DRC 报告 | `drc.check` 退回几何自查，同网络相接铜可能被当间距违规，正常板反报 FAIL | 只要文件被识别为 DRC，0 条也返回 `ok=True,count=0`，并标记“已识别且 0 违规” |
| OPEN-002 | `src/pad_geometry.py:918-924` | `align_offset` 用 Draw Size 左下角减板框 bbox 左下角当平移量，再用“焊盘落在图幅内”验证；两者不是同一对象 | 图框边距、负原点、板不在 0,0 | 焊盘整体错误平移，铜箔距离全部失真，且 `verified=True` | 用同名对象 bbox 标定；交叉验证平移后与快照铜箔重合率，不能只验证在图幅内 |
| OPEN-003 | `src/pick.py:226-242` | Excellon 解析先 `continue` 掉 `;` 注释行，再匹配 `METRIC/FILE_FORMAT`；很多导出把元数据写成 `;METRIC`、`;FILE_FORMAT=2:4` | 注释式元数据的钻孔文件 | 全部孔坐标按 10^decimals 或 25.4 倍错误，定位脚本跳到板外 | 先剥离行首 `;` 再识别元数据；注释行只在确认非元数据后才跳过 |
| OPEN-004 | `src/pick.py:684-686` | `locate.scr` 单位已能取设计单位，但 `find_sources`/混源文件仍可能缺失设计单位上下文 | 直接调用 `write_report` 的旧代码路径 | `zoom center` 单位可能仍不匹配 | 所有调用方显式传 `design_unit`；默认从 BRD 读取 |
| OPEN-005 | `src/brd_analysis/cat_mcad.py:857-869` | IDF/NC Route 基于 `facts.outline_polylines`，只保留最外一圈；内层开槽/内孔被丢弃 | 板上有内部开槽/内孔（demo 的方槽可复现） | 报告写“无开槽”，NC Route 只铣外框 | 保留所有 OUTLINE 连通环，区分外环/内环；两个能力都用全部环 |
| OPEN-006 | `src/ui/tasks.py:59` | 回调异常时调用 `self.widget.report_callback_exception`，但 `Toplevel` 没有该属性；异常后不再续期 `_poll`，轮询永久停止 | `DFMDialog(master)` 非 standalone 模式或任一回调抛异常 | 任务永不完成，按钮一直禁用 | 改用 `widget.winfo_toplevel().report_callback_exception`；用 `try/finally` 保证只要 `alive` 就继续轮询 |
| OPEN-007 | `src/ui/main_window.py:114-119` + `src/checklist_auto_fill.py:181` | 关闭窗口不等待后台 Checklist 线程；`wb.save` 非原子写 | 运行中关闭窗口/进程退出 | 输出 xlsx/报告可能截断损坏 | 记录线程并确认等待；xlsx 先写临时文件再 `os.replace` |
| OPEN-008 | `src/dfm_rules.py:192-215, 520-534` | 所有 INFO 项被转成“低”严重度 issue；`compute_risk_score` 只有低风险时判“可投产” | 大量缺数据但无 FAIL/WARN | 覆盖率很低时报告仍写“仅低优先级提示，可投产”，与“缺数据≠通过”矛盾 | INFO 不计入风险扣分，或风险结论先看覆盖率/缺失项 |
| OPEN-009 | `main.py:474` 等所有 `print` | Windows GBK 控制台输出 `Ø` 等字符抛 `UnicodeEncodeError` | `--analyze` 分析日志含非 GBK 字符 | 分析中断，报告不生成 | 统一 `safe_print`（`sys.stdout.reconfigure(errors="replace")`）或写 UTF-8 日志 |
| OPEN-010 | `main.py:129-133, 237-253` | 显式传入但不存在/拼错的 `--checklist` 被静默跳过；`run_pipeline` 内部错误不返回非零退出码 | CLI 自动化 | 用户以为已生成报告，实际无输出；脚本无法发现失败 | 显式输入做存在性校验并 `sys.exit(1)`；`main()` 根据返回状态设置退出码 |
| OPEN-011 | `main.py:239-240` | `basename(...).replace(".xlsx","_filled.xlsx")` 区分大小写且任意子串替换 | 模板为 `.XLSX`/`.xlsm`，输出目录等于模板目录 | 可能直接覆盖原 Checklist；`.xlsm` 丢宏 | 用 `os.path.splitext`；显式拒绝输出路径等于输入路径 |
| OPEN-012 | `main.py:496`、`src/ui/analyze_dialog.py:399` | 用 BRD `design_name` 直接拼报告文件名，未过滤 Windows 非法字符 | 板名含 `: / \ * ? " < > |` 或超长 | 报告写入失败或落到意外子目录 | 统一 `re.sub(r'[\\/:*?"<>|\r\n]', "_", name)` 并截断 |
| OPEN-013 | `src/pick.py:258-266` | Excellon 只在 X、Y 都不带小数点时整体缩放；一轴带点、一轴整数时整数轴按字面值 | 混合格式坐标行 | 孔位严重偏移 | 逐轴判断是否带小数点并分别缩放 |
| OPEN-014 | `src/component_stats.py:100-119` | 含 `PADSTACK+REFDES` 但无 `W_DBU/H_DBU` 的 CSV（`evan_holes.csv`）被判成 `components` | 手动把 holes 文件加入统计来源 | PIN 行被当元件坐标，覆盖真实元件中心，产生虚假坐标冲突 | 有 `PADSTACK` 且无 W/H 时归为 holes |
| OPEN-015 | `src/pads.py:84-107` | `min_d_mm/max_d_mm` 取全部焊盘（含阻焊/非钢网）；`detect_test_points` 完全没用 `d_min/d_max`，`count` 直接取全部钢网圆盘 | 板上有小阻焊开窗/散热过孔开窗，或钢网圆盘超范围 | 测试点直径假 FAIL/PASS，数量把超范围圆盘也算进去 | 只统计 `stencil=True` 且直径在 `[d_min,d_max]` 的焊盘 |
| OPEN-016 | `src/brd_analysis/cat_reports.py:728` + `cat_database.py:249-254` | 用 `DiffPair.xml` 的差分对**对象名**直接比较网络名；实际网络名常为 `ETH_RX_P/N` | DiffPair 对象名与成员网络名不同 | Net Report 把差分网络标成信号，netlist 差分列自相矛盾 | 解析 DiffPair 成员网络，或使用对象与网络的关联 |
| OPEN-017 | `src/brd_analysis/cat_drc.py:291-303` | 导入 DRC 报告匹配不到关键字就 PASS，忽略 `unrecognized_blocks` | 报告有解析失败块或类别名不同 | 存在未识别短路/开路仍假 PASS | `unrecognized_blocks>0` 至少降级 WARN 并列出摘要 |
| OPEN-018 | `src/pad_geometry.py:692-697` | 焊盘 side=TOP/BOTTOM 时只搜同面外层铜；通孔焊盘从 SKILL 导出的 side 仍是元件面 | 通孔/连接器引脚靠近内层铜 | 焊盘到内层铜距离漏测，报告写成“>搜索半径” | 通孔焊盘对全部 ETCH 层测量 |

### P2

| ID | 位置 | 问题 | 影响 | 建议 |
|---|---|---|---|---|
| OPEN-020 | `src/brd_analysis/geom.py:49-51` | 只要 subclass 含 `OUTLINE` 且 class 不是 `ASSEMBLY` 就判板框，`PACKAGE GEOMETRY/OUTLINE` 被当板框 | 器件外框被算成开槽；Artwork 多出 `OUTLINE_2.gbr` | 仅 `BOARD GEOMETRY` 的 OUTLINE/DESIGN_OUTLINE 归板框 |
| OPEN-021 | `src/brd_analysis/geom.py:142-154` | 圆弧角度差被夹到 `[-π,π]`，>180° 按短弧算；整圆退化为直线 | 线长/铜箔长度/阻抗/电阻全部偏小 | 按真实起止角方向与跨度计算，整圆单独处理 |
| OPEN-022 | `src/brd_analysis/facts.py:219-230` | 对逐段板边折线分别做鞋带公式并取最大值，不是闭合板框面积；矩形板面积为 0 | “板框多边形面积”指标不可信 | 先按端点串成闭合环再算面积 |
| OPEN-023 | `src/design_geometry.py:312-314` | 板框 bbox 只用 `points`；圆弧的 `points` 是圆心，`endpts` 才是端点 | 圆角板尺寸少算圆角；纯圆板直接判无效 | 用含 endpts/radius 的折线求 bbox |
| OPEN-024 | `src/pad_geometry.py:333-337` | 同中心去重键不含 layer/side；尺寸差 `<0.2mm` 才去重 | 上下层通孔焊盘合并，引脚数减半；阻焊开窗差 0.2mm 时重复计数、间距变负 | 去重键加入 side/layer；同中心按钢网>焊盘>阻焊唯一保留 |
| OPEN-025 | `src/pad_geometry.py:578-607` | `closed_edges` 在所有层循环外初始化，边键不含层 | 跨层相同边被误判已使用，整层铜箔/走线漏计 | 每层重置或键加入 layer |
| OPEN-026 | `src/pad_metrics.py:197-199` | 快照焊盘转 metrics 时只保留 w/h，丢失圆形 `d/shape` | 圆被当外接正方形，最小焊盘间距偏小，假 FAIL | 保留 d/shape 或统一走 `_as_metric_pad` |
| OPEN-027 | `src/pick.py:428-443` | `field.startswith("min_annular_ring")` 同时命中 `min_annular_ring_laser`；两种孔环检查都用全部孔 | 激光孔/机械孔互相混入，命中数量和坐标错误 | 按孔类型过滤后再匹配 |
| OPEN-028 | `src/pick.py:162-197` | Placement 单位只认 `mils/millimeter/inch`，`Units: mm` 按 mils；坏行 `float` 无保护 | 坐标放大 25.4 倍；坏文件令统计线程崩溃 | 补 `mm/millimeters/um`；逐行 try/except |
| OPEN-029 | `src/component_stats.py:136-142, 384` | 面别只归一化 `T/TOP/BOTTOM`，`BOT/BTM` 原样返回；统计只计 TOP/BOTTOM/未知 | BOTTOM 器件从面别统计消失 | 补全别名，未识别统一归“未知” |
| OPEN-030 | `src/net_locate.py:355-369` | 任何 `_P/_N` 同前缀网络都推断为差分对，未参考分类/DiffPair | `RESET_N/RESET_P` 等非差分网络被标差分 | 仅明确差分规则或分类命中时配对 |
| OPEN-031 | `src/dfm_coverage.py:62-76` | 能力项映射字段错误：`blind_via_fill_aspect` 映射到 `plug_hole_diameter`，`plug_aspect_ratio` 也映射到孔径并按 `le` 比较 | 拿孔径和纵横比比大小，产生无意义 PASS/FAIL | 映射到 `blind_via_fill_aspect` 等真实设计字段 |
| OPEN-032 | `src/dfm_coverage.py:114-123` | `_flatten` 不递归 list，分档能力表（铜厚/孔径分档）完全不计入覆盖统计 | “能力库共 N 项”少算，覆盖率口径失真 | 递归 list，或显式展平分档表 |
| OPEN-033 | `src/brd_design.py:128-145` | `_find_via_list` 在 `want` 或旧副本 `got` 为 None 时不比较，直接接受旧副本 | 可能从另一块板的旧归档补出 VIA_LIST | 无法确认同一块板时不要回退 |
| OPEN-034 | `src/brd_design.py:534-536` | 钻孔表跨度为空/未识别时，PTH 行被归入 `blind` | 最小激光孔取到普通通孔，激光孔能力判错 | 无跨度证据时单列“待确认”，不要归盲孔 |
| OPEN-035 | `src/brd_raw.py:45-49,175-199` | 钻孔表解析要求孔径带小数点、数量至少 2 位；`8` mil 孔、数量 1–9 会被跳过 | 钻孔行漏统计，`hole_count` 不完整 | 孔径支持整数；数量支持 1 位 |
| OPEN-036 | `src/signal_classifier.py:134-145` | ETHERNET 的 `_TX/_RX` 模式优先级高于 UART，`UART_TX` 被分为 ETHERNET | 网络类/约束/阻抗对象错误 | 调整优先级或给前缀更强的规则 |
| OPEN-037 | `src/design_inputs/reconcile.py` | `brd_component_records` 只提供位号/前缀/类别/封装；`fields_compared` 却声称比较 part_number/value/MPN，实际永远无冲突 | BOM 属性差异检查形同虚设，报告给人“已核对”的错觉 | 从 PartDefn/Gate 补料号/值，或从 fields_compared 移除未提供字段 |
| OPEN-038 | `src/design_inputs/framework.py:102-104` | 补充资料只捕获 `OSError/ValueError`；适配器若抛其他异常会中断整个 `load_project` | 某份补充文件异常导致主 BRD 结果也拿不到 | 捕获 `Exception` 并记录为单个 supplement error |
| OPEN-039 | `src/design_inputs/framework.py:63-77` | `_infer` 把任何目录/zip/tar 都当 ODB++ | Gerber 文件夹被误判 | 目录内先探测 matrix/matrix 或 Gerber 扩展名 |
| OPEN-040 | `src/dfm_engine.py:934-972` | `validate_constraints` 对 `None` 值直接比较，抛 `TypeError` | 旧接口收到缺失值即崩溃 | 先 `_num` 过滤 `None` |
| OPEN-041 | `src/dfm_engine.py:296`、`dfm_rules.py:503`、`checklist_pipeline.py:162` | `copper_oz = max(1.0, copper_um/35)` 把 0.5oz/17.5um 强制抬到 1.0oz | 薄铜板阻焊桥按 1oz 档判定 | 按实际换算，或按能力库档位取最近合法值 |
| OPEN-042 | `src/brd_raw.py:336-356,474-477` | QuickView 单位只区分 mil/其它；`inch` 被当 mm，`scan_brd_raw` 回退也按 1.0 | inch 板所有尺寸错 25.4 倍 | QuickView 单位表补 inch，回退按真实单位换算 |
| OPEN-043 | `src/dfm_report.py` 多处 / `src/workbench_report.py:160-165` | 导出先替换 txt/json、最后替换 xlsx，失败不回滚；旧 sidecar 可能残留 | 目录里出现新旧混合报告 | 全部产物在临时目录生成后统一提交，失败回滚 |
| OPEN-044 | `src/ui/main_window.py:174-176,297,302-304,364-366` | 后台线程调用 `root.after`；窗口销毁后回调被丢弃，`_finish` 只捕获 `RuntimeError` | 主线程忙时卡顿；异常时收尾失败 | 后台只入 queue，主线程定时轮询消费 |
| OPEN-045 | `src/ui/smt_panel.py:12-15` | `DESIGN_UNITS` 漏 `test_pad_2wire`、塞孔深度等字段 | SMT 规则引用这些字段时永远“数据不足” | 从 `DFM_FIELDS + FIELD_META` 全量派生单位 |
| OPEN-046 | `src/ui/widgets.py:89-94` | `bind_all/unbind_all("<MouseWheel>")` 会清掉全应用滚轮绑定 | 指针在表单上时滚轮失效 | 只解绑自己注册的 funcid |
| OPEN-047 | `src/ui/analyze_dialog.py:354-368` | 工作线程调用 `self.top.after`；关闭窗口后仍可能回调 | 关闭时线程异常/日志丢失 | 用队列 + 主线程轮询；关闭时置取消标志 |
| OPEN-048 | `src/ui/main_window.py:53`、`evan_brd.py:157` | `report_callback_exception` 赋给 `Toplevel` 无效；独立版又被 `evan_brd` 覆盖 | 子窗异常不进日志，独立版 `_on_callback_error` 失效 | 设置到 `winfo_toplevel()`；组合处理器而不是覆盖 |
| OPEN-049 | `src/ui/prefs.py:26-34` | 偏好文件直接覆盖写，非原子 | 进程被杀/断电后 JSON 损坏，所有路径丢失 | 写临时文件 + `os.replace` |
| OPEN-050 | `src/brd_analysis/cat_drc.py:195-198,212-214` | 每层只取 `bad[:50]`，却用其长度当违规段数 | 违规数被截断，报告数字偏小 | 单独维护真实总数，仅明细截断 |
| OPEN-051 | `src/brd_analysis/cat_electrical.py:813-815` | 无网表时明细只追加电源/地网络，尽管已从 Net.xml 读到全部网络名 | 20 个信号网络在“网络清单”中消失 | 无网表也输出全部网络名，引脚数留空 |
| OPEN-052 | `src/brd_analysis/cat_assembly.py:611-613,642` | 无位号 BOM 汇总数量被计入“贴装数量合计”，与 Part.xml 位号数相加 | 数量虚高，SMT 多备料 | 无位号数量单列且不计入合计 |
| OPEN-053 | `src/brd_analysis/__init__.py:68-72` | `write_reports` 默认 base_name 从 bundle 顶层取 `brd_path/input_path`，实际在 `bundle["board"]["brd_path"]` | 多板报告同名互相覆盖 | 从 board 子字典取，或 runner 顶层补字段 |
| OPEN-054 | `src/brd_analysis/imports.py:167-174` | DRC 报告标题块被计入 `unrecognized_blocks` | 正常报告也提示“有 1 段未解析”，降低可信度 | 跳过标题/汇总块，仅对疑似违规块计数 |
| OPEN-055 | `src/brd_analysis/cat_mcad.py:711-712,923-925,1016-1018,1172-1174` | DXF/IDF/IDX/STEP 写盘未捕获 `OSError` | 输出目录只读/磁盘满时整个能力“运行失败”，已算结果丢失 | 写盘失败降级为 WARN 并保留分析结果 |
| OPEN-056 | `src/brd_analysis/cat_database.py:1229-1246` | 差异行上限 400、展示 300、警告阈值文案不一致 | 301–400 行静默少列 | 统一阈值并提示截断数 |
| OPEN-057 | `src/design_inputs/bom.py:18,31-37` | `dnp` 未知值原样返回字符串（如 `X`），不是布尔 | 下游 JSON/报告字段类型不一致 | 未知值保留原文但增加 `dnp_state=unknown`，不要伪装布尔 |
| OPEN-058 | `src/checklist_reader.py:41-85` | `Power ... Check` 判断早于 `Power + GND`，`Power and GND Check` 被归 power 而非 power_gnd | 规则分类偏差 | 调整判断顺序 |
| OPEN-059 | `src/pcb_reader.py:123-150` | 层数用 `max(元数据, len(层名))`；层名含额外 L 层时可能多算 | 层数偏大 | 以元数据为准，层名只做异常提示 |
| OPEN-060 | `src/rule_engine.py:168-170` | 配置缺少 `differential_impedance.default` 时 KeyError/None 下标 | 换配置后立即崩溃 | `.get` 链 + 默认值 |
| OPEN-061 | `src/dfm_inputs.py:17,401` | `BRD_COMPONENT_CAP` 只在旧格式分支使用，新格式无截断 | 常量语义混乱，210 行注释误导 | 删除或真正参与截断判断 |
| OPEN-062 | `src/dfm_inputs.py:277-278` | 表面处理选项 `hard_gold` 与解析结果 `HARD_GOLD` 大小写不一致 | UI 选项值与 BRD 值不匹配 | 统一枚举 |
| OPEN-063 | `src/component_stats.py` / `net_locate.py` 轮询 | `_poll` 中回调抛异常时直接 return，不再续期，轮询永久停止 | 后续任务结果永不显示 | 用 `try/finally` 保证续期 |

### P3（清理/文案/性能）

| ID | 位置 | 问题 |
|---|---|---|
| CLEAN-001 | `src/dfm_engine.py:917` | `sm` 赋值未使用（ruff F841） |
| CLEAN-002 | `src/report_generator.py:23` | `pass_fill` 赋值未使用（ruff F841） |
| CLEAN-003 | `src/brd_analysis/cat_drc.py:58`、`cat_reports.py:180,750,1094` | 无占位符 f-string |
| CLEAN-004 | `src/brd_analysis/cat_mcad.py:28`、`cat_physical.py:11,15`、`writers.py:20,21`、`checklist_reader.py:7`、`dfm_engine.py:14`、`dfm_report.py:14,20` | 未使用 import |
| CLEAN-005 | `src/brd_analysis/cat_reports.py:382-383,603-606` | `_merge_imported` 直接 append 模块级列清单，跨调用污染 |
| CLEAN-006 | `src/brd_analysis/cat_manufacturing.py:776-788` | 把逐段板边当独立轮廓，矩形板报“4 条轮廓，全部闭合=否” |
| CLEAN-007 | `src/allegro_skill.py:123-125` | `evan_pin_box` 只检查 `(caddr b)`，却用 `(cadddr b)` 计算高度 |
| CLEAN-008 | `src/allegro_skill.py:202-213` | 圆弧走线只导出两端点，网络长度/bbox 漏弧顶 |
| CLEAN-009 | `src/pad_geometry.py:177-194` | 焊盘栈名取第一个 `A x B`，`0603X2` 会给出 15.3×0.05mm 的假尺寸 |
| CLEAN-010 | `src/pad_geometry.py:447-449,519-526` | 无位号焊盘 5mm 并查集聚类 + O(n²)，器件可能被合并、界面卡死 |
| CLEAN-011 | `src/design_geometry.py:71-75,89-91,164-167` | 只校验 `x`，缺 `y` 时 `KeyError/TypeError` |
| CLEAN-012 | `src/silkscreen.py:99-101` | 只认 subclass `OUTLINE`，不认 `DESIGN_OUTLINE`，与 design_geometry 口径不一致 |
| CLEAN-013 | `src/ui/main_window.py:255-258` | 演示运行不再清空路径后，偏好记忆问题已消除；但仍建议把“演示态”与“上次输入”分键保存 |
| CLEAN-014 | `evan_brd.py:102-114` | CLI 日志在 `try` 外打开且固定写 exe 目录；只读目录安装时直接退出 |
| CLEAN-015 | `src/ui/component_stats_dialog.py:268-283`、`net_locate_dialog.py:316-331` | 关闭窗口时未取消搜索/画布/轮询 after 回调 |
| CLEAN-016 | `src/pick.py:162-197` | Placement 正则允许 `1..2`，`float` 无保护 |
| CLEAN-017 | `src/dfm_report.py:353` | 文本报告板名兜底逻辑已修；Excel Main 页“单板名”仍可能为空，建议统一取 `analysis.board_name` |
| CLEAN-018 | `src/workbench_report.py:131` | 临时目录建在目标父目录，父目录不存在时直接失败 |

---

## 4. 关键结论（需要业务侧确认）

1. **“缺数据 ≠ 通过”目前只落实在部分链路**：`analyze_design` 的 verdict 会考虑覆盖率，但 `dfm_rules.compute_risk_score` 仍可能把大量 INFO 当成低风险并给出“可投产”。建议在报告层增加硬门槛：覆盖率 < 60% 时禁止出现“可投产/通过”字样。
2. **单位/坐标是最集中的风险区**：BRD 数据库单位、约束对象单位、钻孔表单位、Draw Size 单位、导出文件单位、Allegro 脚本单位是 6 套不同口径。本轮修了 3 处（BRD Banner、Draw Size、locate.scr），仍有 Excellon/Placement 与快照 bbox 问题。
3. **BOM 双向核对目前只核对位号与封装**：`fields_compared` 宣称的料号/值/MPN 实际没有 BRD 侧数据，报告不应让人误以为已核对。
4. **旧格式 BRD 回退链路**：`pcb_reader` 返回的 `components` 是 list，与新版 dict 结构不同。本轮在 analyze/report 做了兼容，但其他读取 `_components` 的模块仍需统一。
5. **后台线程纪律**：`main_window` 已改为参数快照；`dfm_dialog`/`analyze_dialog` 仍有 `after()` 跨线程调用，建议统一成“worker → queue → 主线程轮询”。

---

## 5. 回归与验收

修复后执行：

```powershell
python -m compileall -q src main.py
python -m pytest tools -q
python -m ruff check src main.py --select F821,F811,F841
python main.py --dfm template
python main.py --dfm dfm_design_sample.json --output <临时目录>
python main.py --fill-checklist demo --output <临时目录>
python main.py --input-project demo\DEMO_BOARD.brd --bom <BOM.csv> --input-json <临时文件>
```

结果：

- `compileall`：通过
- `pytest tools -q`：27 passed
- `ruff F821/F811/F841`：仅剩 2 个未使用变量（CLEAN-001/002）
- `--dfm template`：正常输出
- `--dfm dfm_design_sample.json`：正常生成 JSON/XLSX/TXT
- `--fill-checklist demo`：正常生成 filled xlsx + 报告 + txt

---

## 6. 建议的下一步顺序

1. 先确认 OPEN-001（0 违规 DRC）、OPEN-005（内槽丢失）、OPEN-008（缺数据仍可投产）三个会直接影响结论正确性的问题。
2. 再修 OPEN-002/003/013（坐标/单位），并用 demo 板的 mm/mil 两套导出做回归。
3. 之后处理 UI 线程纪律（OPEN-006/007/044/047）与导出原子性。
4. 最后统一清理 P3（死变量、重复列修改、文案）。
