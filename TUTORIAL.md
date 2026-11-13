# Evan / MPA-DFM 使用与开发教程

更新：2026-11-12。当前推荐入口是 **`mpa_dfm.py` / `dist/MPA_DFM.exe`**：同一工作台提供 PCB 制造能力、SMT 工艺能力、BRD 问题定位和网络定位。主窗口已移除 Checklist、器件统计两个按钮；原功能可从兼容入口使用。

**最新 COMP_XY/丝印测试包：`dist/Evan_MPA_DFM_COMP_XY_SILK.exe`**。旧 MPA_DFM.exe、Evan_MPA_DFM_20261112_XY.exe 保留为前轮版本。

**最新 Gerber 工作台包：`dist/Evan_MPA_DFM_GERBER.exe`**。默认在执行分析时后台由 BRD 导出 Gerber 并定位制造问题，无需“打开 BRD 并提取坐标”；原坐标桥接仍可用于器件/对象定位。新增报告隐藏低优先级、独立 PCB 报告开关、English/中文、Dark/Light。见 [GERBER_WORKFLOW.md](docs/GERBER_WORKFLOW.md)。

COMP_XY/丝印版本新增“导入 COMP_XY”和 `evan_coordinates.scr`，完整说明见 [COMP_XY_AND_SILK.md](docs/COMP_XY_AND_SILK.md)。支持 `VERSION/UUNITS/!` 文本，先读器件原点，再用丝印/板框/焊盘对象导出进行位置筛查。

## 1. 先运行起来

### Windows 10/11 源码环境

推荐 64 位 Python 3.12（包含 tkinter），用 VS Code 打开本文件所在项目目录 `D:\Evan\MPA-DFM\MPA-DFM`。

在该目录的 PowerShell 中执行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe mpa_dfm.py
```

不需要激活脚本，因此不会遇到 PowerShell 执行策略问题。VS Code 中通过“Python: Select Interpreter”选择 `.venv\Scripts\python.exe`。

| 依赖 | 用途 |
|---|---|
| tkinter（Python 自带） | 桌面界面、事件循环 |
| openpyxl | Excel 模板读取、报告输出 |
| PyYAML | PCB/SMT/Checklist 能力与规则配置 |
| Jinja2 | 旧约束生成流程的 SKILL 模板 |
| pytest / ruff | 测试与静态检查 |
| PyInstaller | 生成 Windows EXE |
| python-pptx（可选） | `PPT/create_mpa_dfm_smt_ppt.py` 生成介绍 PPT |

只运行程序可安装 `requirements.txt`；开发和打包使用 `requirements-dev.txt`。

### 已打包程序

打开 `dist\MPA_DFM.exe`。单文件程序首次启动需要解压资源。
可以把 `.brd` 拖到 EXE 上，或：

```powershell
.\dist\MPA_DFM.exe "D:\PCB\board.brd"
```

`evan_dfm.py` 是兼容入口；`gui.py`、`evan_brd.py` 是原 Checklist/命令行入口。新功能优先从统一工作台使用。

## 2. PCB 分析与状态显示

1. 在 PCB 页选择 `.brd`，等待“从 BRD 读取”完成。
2. 选择板厂 `hongban`（红版）或 `sihui`（四会）、板型、制程等级和铜厚。
3. 自动读取的参数会带来源；缺失参数按实际数据补填。空白不等于 0，也不等于通过。
4. 点击 **执行 PCB 分析**。
5. 查看“制程能力比对”和“问题 X/Y 坐标”两个结果页签。双击 FAIL/WARN 行可跳转对应坐标；有坐标时可直接打开真实 BRD 定位。

新增 **“BRD 器件 X/Y”** 页签：显示全部导出器件的真实放置原点（位号、X/Y mm、X/Y BRD 单位、面别、角度）。可按位号筛选、双击定位器件或复制坐标命令。该页不是孔位列表，也不是焊盘中心平均值。
只选择 BRD 会离线读已有导出；点击 **“打开 BRD”** 会自动打开 Allegro 并提取器件坐标。完成后直接切到器件 X/Y 页签，同时重新分析。
导出保存在 `<BRD目录>/定位清单/allegro_bridge`，下次启动同一块板可重新发现。
PCB/DFM Excel 增加“BRD器件坐标”工作表，CSV 增加“器件坐标”记录。

状态行为：

| 阶段 | 标识/文字 | 进度条 |
|---|---|---|
| 初始就绪 | 灰色 | 空轨道 |
| 点击执行、后台分析 | 蓝色标识 | 绿色滑动动画 |
| 本次分析完成 | 蓝色“PCB 分析完成” | 蓝色完整条 |
| 失败 | 红色提示 | 停止动画 |
| 输入变化 | 返回待分析状态 | 旧结果失效 |

蓝色完成表示任务已执行完；制造判定仍看 PASS/WARN/FAIL、覆盖率和“可否放行”。
后台分析期间编辑参数会丢弃旧结果，避免把旧板厂或旧参数的分析显示成当前结果。

## 3. 导出 XLSX 或 CSV

顶部“报告导出”旁选择 **`.xlsx`** 或 **`.csv`**，再点击报告按钮。保存对话框也可选择这两种格式。

| 按钮 | 前置条件 | XLSX 输出 | CSV 输出 |
|---|---|---|---|
| PCB 报告 | 当前 PCB 分析完成 | Excel + TXT | 单个 UTF-8 BOM CSV |
| SMT 报告 | 当前 SMT 分析完成 | Excel + JSON | 单个 UTF-8 BOM CSV |
| DFM 综合报告 | 当前 PCB、SMT 均完成且 BRD 一致 | Excel + TXT + JSON | PCB/SMT 共用一张 CSV 表 |

PCB Excel 包括 `Main`、`Issue summary`、`Info`、单项 `IssueN`、**逐项能力**、**问题坐标**、**坐标说明与来源**。
综合 Excel 的 PCB 页签带 `PCB_` 前缀，SMT 页签带 `SMT_` 前缀。

CSV 是单表：用“记录类型”区分分析结论、能力比对、问题坐标、坐标说明；用“范围”区分 PCB/SMT。
同一个问题有多个位置时，每个位置占一行。中文以 UTF-8 BOM 输出，便于 Windows Excel 打开。
Excel 保留多页签、样式、问题链接；CSV 保留表格数据。

## 4. 在 BRD 中输入 X/Y 找问题点

### 4.1 为什么需要坐标导出

`.brd` 数据库确实包含器件、走线和孔位。**当前离线解析器**能读取约束、对象摘要、钻孔表及部分几何快照，但尚不能完整解码数据库里的每个对象坐标。
板级“最小允许线宽”是规则，不代表某条真实走线的位置。因此真实问题定位需要同一版本板的对象级导出。

### 4.2 推荐流程：SKILL 导出

1. 在工作台点击 **生成 Allegro 坐标脚本**，选择专用目录（建议每块板一个目录，如 `D:\PCB\board_exports`）。
2. 程序生成 `evan_pick.il`、`evan_pick.scr` 和说明文件，命令复制到剪贴板。
3. 用 Allegro PCB Editor 打开**同一版本** `.brd`，在 Command 窗口执行复制的两行：

```text
skill load("D:/PCB/board_exports/evan_pick.il")
skill evan_pick_all("D:/PCB/board_exports")
```

4. 查看 `evan_pick.log`，确认元件、孔/引脚和走线数量。不同 Allegro 版本的属性支持可能不同；出现 0 条时以日志为准。
5. 回到工作台，“问题坐标来源”选择该目录，重新执行 PCB 分析。
6. 打开“问题 X/Y 坐标”，或导出报告看“问题坐标”页。“复制坐标命令”可复制命令；双击行会在 Allegro 打开 BRD 并定位。

### 4.2.1 一键打开 BRD、提取坐标与同步视图

工作台新增：

- **打开 BRD**：用 Allegro PCB Editor 打开当前真实板文件，自动提取器件 X/Y 并建立坐标同步连接。
- **打开 BRD 并提取坐标**：打开板并自动执行坐标导出；有孔/走线数据后自动带入导出目录并重新分析。
- **设置 PCB Editor**：自动发现失败时指定 `allegro.exe`，路径保存在用户偏好中。
- **打开 BRD 定位**：在问题坐标页选中有 X/Y 的行后点击，或双击该行。
- **选点同步到 Allegro**：连接后切换行、上一个点/下一个点会更新同一 Allegro 窗口。
- **并排显示**：本程序与启动的 Allegro 窗口左右排列，便于对照能力与真实板图。

连接方式为 Allegro 启动脚本加 SKILL 定时器：Python 只写入数值毫米坐标，Allegro 按当前用户单位换算并缩放到约 5×5mm 区域。状态显示“已定位选中问题点”时表示已收到 Editor 回执，不只是命令已发送。
默认以只读方式打开源 BRD；关闭工作台不会关闭 Allegro。若 Editor 已换板，连接会报告换板状态，需要重新打开当前板。
已有手工打开的 Allegro 窗口不会自动注入脚本；首次从工作台操作会启动专用连接窗口。
许可选择/正在执行的交互命令可能延迟定时器响应，完成许可选择或结束当前命令后再选点。

本机 Allegro 17.4 已实测真实 BRD：自动导出 279 个元件、589 个过孔、7 个通孔引脚、671 个引脚外框、2308 段走线；连续两个坐标更新均收到 LOCATED 回执。

新版导出：

| 文件 | 内容 |
|---|---|
| `evan_components.csv` | 元件原点、旋转、面别 |
| `evan_holes.csv` | 过孔/引脚中心、焊盘栈、网络、位号 |
| `evan_pads.csv` | 引脚中心与外框宽高 |
| `evan_traces.csv` | 走线端点、线宽、层、网络 |
| `evan_pick.log` | 数量和属性读取错误 |

首行为 `UNITS,<设计单位>,<精度>,<DBU/单位>`。新版 `X_UU/Y_UU` 使用 **设计用户单位**；旧格式 `X_DBU/Y_DBU` 仍可读取并换算。
旧版脚本曾把用户单位误标 DBU，已有旧文件请用新版脚本重新导出，不能只改表头掩盖单位差异。

### 4.3 坐标输入示例

报告同时提供：

- `X(mm)/Y(mm)`：统一毫米值；
- `X(BRD)/Y(BRD)`、`BRD坐标单位`：换算为分析时读取到的设计单位；
- `Allegro命令`：例如 `x -100.500000 200.250000`。

假设该行 BRD 单位是 `mil`：

1. 在 Allegro 打开报告来源所指的板。
2. 在 Design Parameters 的 Design 页确认用户单位为 mil；若你已改为 mm，则改用报告的 mm 列。
3. 在 Command 窗口执行：

```text
x -100.500000 200.250000
```

`x X Y` 是一条完整的 X/Y 坐标命令；两轴在同一行输入。它移动/指定坐标点，随后缩放并检查该处对象。
工具保持数据库/导出原点、负坐标及轴向，不自动把板左下角当零点，也不自动镜像。
若 BRD 单位未知，报告不给猜测的设计单位命令；先把 Allegro 设置为 mm，再使用 mm 列。

### 4.4 哪些问题能给坐标

| 检查 | 当前定位方式 |
|---|---|
| 最小/最大机械孔径 | 孔位与刀具尺寸或可解析焊盘栈名匹配，列出所有超能力孔 |
| 机械孔环 | 有孔径及焊盘径的通孔，计算 `(焊盘径 - 孔径)/2`；焊盘栈名称推算需核对实际库 |
| 线宽/颈宽 | SKILL 走线的真实宽度低于能力值，列出端点中点、层和网络；圆弧需结合对象查看 |
| 激光孔/孔环 | 必须有可证明的激光工艺类型；不因“孔小”就当激光孔 |
| 线距、阻焊、板厚、拼板、材料等 | 仅板级值不能确定唯一点；报告明确写“待补几何/坐标”及原因 |

NC Drill `.drl/.nc/.xln` 可作为孔位来源；Placement 可补器件原点，但不能证明线宽/线距问题。
钻孔/Placement 的辅助原点、镜像设置必须与 BRD 一致。SKILL 从当前数据库导出是推荐来源。
整数 Excellon 坐标需 `FILE_FORMAT` 或 `000.000` 声明；缺格式时不猜坐标。

## 5. SMT、Checklist 与资料库

### SMT

1. 在 SMT 页导入 YAML；参考 `config/smt_capability_template.yaml`。
2. 选择产线/工艺 profile，输入已确认设计值与过程测量值。
3. `scope: design` 可引用 PCB 已确认同名值；`scope: process` 需要过程实测，不能拿设计尺寸代替。
4. 执行 SMT 分析，再导出 SMT/DFM 综合报告。

`schema_version: 1`；`meta.factory/revision` 必填；规则需 `id/field/label/unit/op`。
运算支持 `>=`、`<=`、`range`；`value` 或 `min/max` 为 null 时代表厂商未提供能力，不能判 PASS。

### Checklist

运行 `python gui.py` 打开原 Checklist 窗口 → 选择 BRD 和 `.xlsx` 模板 → 检查模板 → 填写 Checklist 报告。
模板按表头识别列，按条目匹配规则；保留已有填写。输出 `_filled.xlsx`、填写报告 XLSX 和 TXT。
生成期间关闭窗口会提示等待，后台线程不访问 Tk 变量/控件。

### BRD↔BOM 资料库

在程序旁创建/使用 `store-brd`、`store-bom`，分别放 BRD、BOM XLSX/CSV。
Checklist 窗口的“BRD↔BOM 联动检查”支持配对、位号双向核对及封装差异；缺 BRD 属性时不能声称已核对料号/值。
详细说明见 `docs/资料库联动检查.md`。

### 八大类 BRD 分析

Checklist 窗口可打开“BRD 分析与输出”，涵盖 DRC、物理、电气、制造、报告、装配、MCAD、数据库 65 项能力。
这些能力和 PCB 制程参数比对是两条相关但不同的分析流程；支持选项过滤和多格式产物。

## 6. 开发时的数据流

```text
mpa_dfm.py -> evan_dfm.main -> DFMDialog
    BRD -> brd_design / pcb_reader -> ParamPanel / dfm_inputs
    主线程读取参数快照 -> TaskRunner 工作线程
        dfm_engine.analyze_design -> dfm_rules / dfm_coverage
        pcb_locations.attach_locations -> pick / net_locate
    queue -> Tk 主线程 -> ResultPanel
    点击导出 -> 深拷贝结果 -> workbench_report -> dfm_report / smt_engine

Checklist -> MainWindow -> checklist_pipeline -> 模板/自动规则/填写/报告
八大类分析 -> brd_analysis.Context -> catalog/runner -> cat_* -> writers
```

核心约定：

1. **界面负责交互，引擎负责计算，报告负责序列化**。新增制造规则不要写在按钮回调里。
2. **所有 Tk 操作在主线程**。工作线程接收普通 dict，结果和日志走队列。
3. **结果与输入版本绑定**。修改输入时作废旧结果；完成回调校验 revision。
4. **缺数据不是通过**。每个结论要有数值、单位、来源和可验证说明。
5. **坐标可追溯**。保存来源、单位和原点口径，不擅自平移/镜像。
6. **报告使用快照**。用户继续编辑不会改变已经开始导出的报告。

## 7. 如何添加功能

### 7.1 增加 PCB 参数/规则

以“增加一个有实际制造意义的最小尺寸字段”为例：

1. 在 `src/dfm_inputs.py` 的 `DFM_FIELDS` 增加字段，在 `FIELD_META` 标明单位/BRD 属性。
2. 若可从 BRD 提取，在 `brd_design.py`/`dfm_inputs.py` 填入值与 `_sources` 依据；不可提取就留空。
3. 在相应厂商 YAML 加能力字段，使用正确单位。不要随意替厂商填默认能力。
4. 在 `dfm_engine.analyze_design()` 调用 `add_check(...)`，设置 `field`；最小允许值用 `higher_is_better=True`，最大允许值用 False。
5. 若需能力库全覆盖显示，在 `dfm_coverage.py` 加映射；不可把孔径字段映射到纵横比规则。
6. 若要定位，在 `pcb_locations.py` 增加对象级匹配。先明确需要哪个导出字段，再定义坐标点代表什么。
7. 添加“低于/等于/高于边界”“缺数据”“单位换算”的测试，运行 pytest。

### 7.2 调整界面

- 配色/字体/Windows 图标：`src/ui/theme.py`。
- 页眉、报告格式、任务状态和坐标来源按钮：`src/ui/dfm_dialog.py`。
- 参数输入：`src/ui/dfm_params.py`。
- 结果表和坐标复制：`src/ui/dfm_results.py`。
- 新后台操作用 `TaskRunner.submit(key, work, done, failed)`；在 submit 前读取 Tk 变量。

### 7.3 增加 SMT 厂商

复制 SMT YAML 模板，填写 `meta`、profile、规则方向、能力值、单位、严重度、来源。
导入后确认 `null`、单位冲突、重复 ID、缺输入处理正确。一般只需配置，不必改界面。

### 7.4 增加八大类能力

在 `src/brd_analysis/catalog.py` 注册能力，在对应 `cat_*.py` 实现处理函数，返回 `Result`。
设置 `summary/status/metrics/rows/sources`；缺材料调用 unavailable 并说明“缺什么/如何导出”。
由 runner 执行、writers 出表，不在能力函数中创建 Tk 窗口。

### 调试顺序

先对引擎的小输入运行，再用 demo 和真实导出文件回归，最后测试 GUI/EXE。
VS Code F5 可选“Python File”，当前文件选择 `mpa_dfm.py`；计算问题在引擎中设断点。
后台异常、Allegro 属性读取异常分别看界面消息/`Evan_DFM_crash.log`、`evan_pick.log`。

## 8. 每个 Python 文件的作用

以下列出当前项目全部源码 Python 文件；`__pycache__`、build/dist 的生成文件不属于开发入口。

### 根目录与配置包

| 文件 | 作用 |
|---|---|
| `mpa_dfm.py` | 统一工作台启动入口、顶层崩溃日志 |
| `evan_dfm.py` | 创建 Tk、识别命令行 BRD 路径、启动 DFMDialog；兼容独立 DFM 程序 |
| `evan_brd.py` | 原 BRD/Checklist 打包入口，区分 GUI/CLI 并记录异常 |
| `gui.py` | 旧 GUI 入口及兼容导出，启动 Checklist 主窗口 |
| `main.py` | 命令行参数和各流程调度：约束、DFM JSON、Checklist、定位、八类分析、多格式输入、资料库 |
| `config/__init__.py` | 标识配置包；配置内容主要在 YAML/JSON 中 |

### `src/` 核心模块

| 文件 | 作用 |
|---|---|
| `src/__init__.py` | 核心源码包入口 |
| `src/brd_design.py` | 读取 BRD 内嵌归档/XML、抽取设计参数、转换为 DFM 输入 |
| `src/brd_raw.py` | 扫描原始二进制的单位、层名、钻孔表、焊盘栈命名和制程说明 |
| `src/pcb_reader.py` | 旧格式 BRD 的网络/层/器件读取兜底 |
| `src/brd_components.py` | 从 Part XML 提取位号、前缀类别和器件摘要 |
| `src/design_geometry.py` | 读取/缓存几何快照、板框/KEEPIN 距离、比例标定 |
| `src/pads.py` | 快照焊盘读取、圆形/钢网焊盘与测试点候选统计 |
| `src/pad_geometry.py` | 焊盘形状、器件/引脚几何及到铜箔/走线的距离分析 |
| `src/pad_metrics.py` | 焊盘间距等指标计算与 DFM 参数接入 |
| `src/silkscreen.py` | 丝印线宽、尺寸和到板框距离统计 |
| `src/silk_position.py` | 生成丝印/板框 SKILL 导出，读取实际图形坐标并检查线宽、板边距离、焊盘近距候选 |
| `src/gerber_export.py` | 在独立临时目录调用 Cadence artwork.exe 后台导出真实制版 Gerber，保留源板和日志 |
| `src/gerber_analysis.py` | Gerbonara/Shapely 还原制造图形、计算问题坐标并生成 SVG/HTML 预览 |
| `src/dfm_inputs.py` | DFM 字段/单位元数据、BRD 值映射、来源状态管理 |
| `src/dfm_engine.py` | 厂商能力读取、铜厚分档、PCB 参数判定、覆盖率、旧约束校验 |
| `src/dfm_coverage.py` | 遍历能力库并显示已检查/缺输入/尚未接入的能力项 |
| `src/dfm_rules.py` | 能力结果转问题清单、附加工艺/装配问题、风险和工艺统计 |
| `src/pcb_locations.py` | 新问题坐标桥接：孔/走线匹配、mm/BRD 换算、定位命令、来源说明 |
| `src/pick.py` | SKILL/Placement/Excellon 解析、坐标来源发现、孔位匹配、独立定位报告/CLI |
| `src/allegro_skill.py` | 生成 Allegro 坐标导出 IL/SCR/README；新版 *_UU 用户单位 |
| `src/allegro_bridge.py` | 自动查找 Editor、打开真实 BRD、数值坐标桥接、定位回执、Windows 并排显示 |
| `src/net_locate.py` | 网络名/差分对与带 NET 的导出几何归并，网络 bbox、长度、CSV |
| `src/component_stats.py` | 合并 Part/元件/Placement/焊盘导出，器件分类、封装和坐标统计 |
| `src/dfm_report.py` | PCB Excel/TXT，问题详情、逐项能力和坐标页 |
| `src/workbench_report.py` | 统一 PCB/SMT/综合 XLSX/CSV API、报告快照、临时目录生成 |
| `src/smt_engine.py` | SMT YAML 校验、单位换算、逐项判定、SMT 报告和工作表 |
| `src/checklist_template.py` | 模板表头/列识别、规则配置、演示模板 |
| `src/checklist_auto_rules.py` | Checklist 条目规则匹配和 PASS/FAIL/VERIFY/MANUAL/N/A 判定 |
| `src/checklist_auto_fill.py` | 把自动判定填入 Excel 模板、保留已有内容 |
| `src/checklist_pipeline.py` | BRD→事实/DFM→识别模板→填写→报告的完整流程 |
| `src/checklist_report.py` | Checklist 填写过程与判定结果的 Excel/TXT 报告 |
| `src/checklist_reader.py` | 旧 Checklist 规则读取/分类与默认规则合并 |
| `src/signal_classifier.py` | 按网络命名识别信号类别、差分对和默认阻抗 |
| `src/rule_engine.py` | 信号类对应的线宽/间距/阻抗约束计算 |
| `src/skill_generator.py` | 根据计算出的约束生成 Allegro 约束设置 SKILL（区别于坐标导出） |
| `src/report_generator.py` | 旧约束生成流程的 Excel/TXT 汇总 |
| `src/store_report.py` | BRD↔BOM 配对与双向检查的 Excel/CSV/JSON/TXT |
| `src/ui_theme.py` | 旧主题导入路径的兼容层 |

### `src/design_inputs/` 多格式输入

| 文件 | 作用 |
|---|---|
| `__init__.py` | 对外导出多格式输入/核对 API |
| `framework.py` | 统一项目输入适配、格式识别、补充资料及输出 JSON |
| `bom.py` | Excel/CSV BOM 表头归一化、位号展开、数量/装配状态、导出 |
| `gerber.py` | 基础 RS-274X/X2 角色、孔径、中心线/闪光解析；未做完整铜面布尔运算 |
| `manufacturing.py` | ODB++ 目录/归档结构及 STEP 文本头识别；未接 CAD 实体内核 |
| `reconcile.py` | BRD/BOM 位号、可提供属性的双向差异核对 |
| `store.py` | 扫描 store-brd/store-bom、料号/版本配对、逐项联动结论 |

### `src/brd_analysis/` 八大类能力

| 文件 | 作用 |
|---|---|
| `__init__.py` | `analyze/write_reports` 高层 API |
| `catalog.py` | 能力定义/ID/类别注册表 |
| `model.py` | Capability/Result 等结构与状态 |
| `context.py` | 载入 BRD、输出目录、补充文件，缓存共享上下文 |
| `facts.py` | 提供网络、孔、器件、板框等事实数据与来源 |
| `geom.py` | 几何分类、坐标/线段/圆弧辅助计算 |
| `imports.py` | 导入 Allegro 报告、DRC、网表等文本资料 |
| `runner.py` | 加载能力模块、过滤/执行能力、统计结果与产物 |
| `writers.py` | 能力 bundle 的 Excel/TXT/HTML/JSON/CSV 输出 |
| `cat_drc.py` | 线宽、间距、孔、短路/开路/未连接等检查 |
| `cat_physical.py` | 层叠、板框、焊盘、摆放及物理参数分析 |
| `cat_electrical.py` | 网络/阻抗等电气与信号信息分析 |
| `cat_manufacturing.py` | Gerber/钻孔/网表等制造产物及相关核对 |
| `cat_reports.py` | 元件、网络、钻孔、材料等专项报告 |
| `cat_assembly.py` | BOM、贴装、装配、焊盘/器件间距等分析 |
| `cat_mcad.py` | DXF/IDF/STEP/IDX 等机械交换及结构分析 |
| `cat_database.py` | 数据库对象、参数、约束、版本差异与导出清单 |

### `src/ui/` 界面模块

| 文件 | 作用 |
|---|---|
| `__init__.py` | 界面对外入口与兼容导出 |
| `theme.py` | 颜色、字体、ttk 样式、DPI、evan.ico、Card/LogView |
| `i18n.py` | 中英文控件/表头/状态映射，保留技术字段与输入值 |
| `widgets.py` | 滚动表单、表格等通用控件 |
| `tasks.py` | 后台工作线程→队列→主线程完成回调及销毁处理 |
| `paths.py` | 源码/EXE 目录、只读资源和默认输出目录 |
| `prefs.py` | APPDATA 下偏好 JSON 读取、原子保存 |
| `fields.py` | 旧 UI 字段定义路径的兼容层 |
| `dfm_dialog.py` | 统一工作台、BRD 读取、状态动画、坐标来源、PCB 调度与导出 |
| `dfm_params.py` | 左侧参数输入、BRD 值带入、确认/显示、设计字典读取 |
| `dfm_results.py` | 能力表、问题 X/Y 表、分批渲染、打开 BRD 定位与前后点切换 |
| `dfm_locate.py` | 独立孔/元件定位清单对话框 |
| `smt_panel.py` | SMT YAML/profile 输入、SMT 执行和结果显示 |
| `main_window.py` | Checklist 主窗口生命周期、队列日志、后台填写流程 |
| `main_panels.py` | Checklist 页眉/状态栏/输入/选项/日志卡片/底部按钮 |
| `analyze_dialog.py` | 八大类能力选择、执行、详情及产物输出对话框 |
| `component_stats_dialog.py` | 器件筛选、分类统计、明细/封装/坐标和报告 |
| `net_locate_dialog.py` | 网络筛选、坐标/包络图、明细和导出 |
| `store_dialog.py` | 资料库路径、配对检查、位号差异详情和导出 |

### 测试、样本和 PPT

| 文件 | 作用 |
|---|---|
| `tools/make_demo_brd.py` | 生成可复现合成 BRD/导出样本；不是商业板文件解析器 |
| `tools/make_evan_icon.py` | 为每个 Windows 图标尺寸单独绘制 EV/AN 两行字标，生成 ICO 和预览；开发依赖 Pillow |
| `tools/generate_coordinates.py` | 生成 COMP_XY、器件/孔/走线、丝印/板框完整导出脚本，可启动 Allegro |
| `tools/test_design_inputs.py` | 多格式输入、BOM、核对适配回归 |
| `tools/test_store_check.py` | 资料库配对、BOM/BRD 和 Placement 补位号回归 |
| `tools/test_smt_engine.py` | SMT 配置、单位、判定、输出回归 |
| `tools/test_workbench_report.py` | 综合报告、导出失败、快照与 BRD 一致性 |
| `tools/test_workbench_ui.py` | Tk 响应、过期结果、状态颜色、导出快照、子窗口回调异常 |
| `tools/test_pcb_locations.py` | 孔匹配、无截断、负原点/用户单位、XLSX/CSV、钻孔格式、零违规 DRC、走线定位 |
| `tools/test_allegro_bridge.py` | Editor 启动参数、数值坐标请求/回执、实际孔径用户单位、无坐标拒绝 |
| `tools/test_comp_xy_silk.py` | COMP_XY 样本格式、镜像/单位、丝印真实线宽/板边、面别和制造结论回归 |
| `tools/test_gerber_dfm.py` | Gerber 图形位置、正负极性、低优先级筛选保持原始结论回归 |
| `tools/selftest_analysis.py` | 65 项能力及制造产物的端到端自检 |
| `tools/selftest_component_stats.py` | 器件合并/分类/面别/引脚/报告自检 |
| `tools/selftest_net_locate.py` | 网络几何、差分对、包络和 CSV 自检 |
| `tools/selftest_ui.py` | 八大类对话框 Tk 冒烟 |
| `tools/selftest_store_ui.py` | 资料库联动对话框 Tk 冒烟 |
| `PPT/create_mpa_dfm_smt_ppt.py` | 生成 PCB/SMT 工作台介绍幻灯片（可选依赖 python-pptx） |

## 9. 验证与命令行

在项目根目录运行（使用你的虚拟环境 Python 替换 python）：

```powershell
python -X utf8 -m pytest tools -q
python -m ruff check . --select E9,F63,F7,F82
python -m compileall -q src tools config PPT main.py gui.py mpa_dfm.py evan_brd.py evan_dfm.py
python -X utf8 tools/selftest_analysis.py
python -X utf8 tools/selftest_component_stats.py
python -X utf8 tools/selftest_net_locate.py
python -X utf8 tools/selftest_ui.py
python -X utf8 tools/selftest_store_ui.py
```

本轮新增桥接测试后共 **55 个 pytest**；能力端到端 35/35、器件统计 24/24、网络定位 25/25 的前轮自检记录见审查文档。
指定静态规则检查语法/未定义名称等高影响问题；全量 ruff 还包含历史风格建议，不能把这些建议当成功能崩溃。

常用 CLI：

```powershell
python main.py --help
python -X utf8 main.py --fill-checklist demo --output output
python -X utf8 main.py --dfm dfm_design_sample.json --output output
python -X utf8 main.py --analyze "D:\PCB\board.brd" --output output
python -m src.pick --brd "D:\PCB\board.brd" --skill --out "D:\PCB\board_exports"
python -m src.pick --brd "D:\PCB\board.brd" --files "D:\PCB\board_exports\evan_holes.csv" --out output
```

Python 中直接导出：

```python
from src.dfm_engine import analyze_design
from src.pcb_locations import attach_locations
from src.workbench_report import export_report

pcb = analyze_design({"min_mech_drill": 0.127}, factory="hongban")
pcb = attach_locations(pcb, brd_path="board.brd", design_unit="mil",
                       extra_dirs=["board_exports"])
export_report("pcb", "output/PCB报告.csv", pcb=pcb)
export_report("pcb", "output/PCB报告.xlsx", pcb=pcb)
```

## 10. Windows 打包与图标

```powershell
python -m PyInstaller --clean --noconfirm MPA_DFM.spec
```

或者运行 `build_evan_brd.bat`。输出为 `dist\MPA_DFM.exe`。
`MPA_DFM.spec` 把 `config`、`evan.ico`、`evan.png` 加入包，并用 evan.ico 作为 EXE 图标。
运行时同样设置 evan.ico 和 Windows AppUserModelID，用于标题栏、任务栏、Alt-Tab。
现有 ICO 含 **16/20/24/32/40/48/64/128/256 px** 原生尺寸；按最新要求使用 **EVAN 横排单行**。窗口按实际 DPI 设置 Windows 小/大图标；原图标保留为 `evan_original.ico`。
新 AppUserModelID 让本次新版运行窗口使用新图标身份；旧固定快捷方式仍需重新固定新 EXE。
程序启用 Windows Per-Monitor DPI 感知并根据屏幕尺寸限制初始窗口大小。

本次已在当前 Windows 11 环境生成 EXE；Windows 10 实机应验证 100%/125%/150% 缩放下窗口、任务栏图标和坐标命令。
更换 EXE 后任务栏旧固定快捷方式可能缓存旧图标，重新固定新 EXE 可更新。

## 11. 常见问题

- **没有问题坐标？** 看“定位状态/说明”；板级约束、材料和工艺值没有唯一点。确认已从当前 BRD 导出、选对目录、重新分析。
- **坐标差 39.37 倍或 1000 倍？** 分别检查 mm/mil、用户单位/DBU。用新版 SKILL 重导，核对表头与 UNITS。
- **钻孔坐标跑到板外？** 核对 NC 原点、镜像、整数格式、单位；优先换用 SKILL。
- **数量重复？** 给每块板独立目录，避免混用另一版板导出；同孔同尺寸已去重，多层跨度保留。
- **没有 Allegro？** 仍可离线分析已支持数据，孔位可读取供应商 NC 文件；对象级位置需相应导出来源。
- **报告存不进去？** 关闭 Excel 对该文件的占用，检查目录可写和路径；导出在临时目录生成后替换目标。
- **界面文字模糊/挤出屏幕？** 检查 Windows 缩放与兼容性属性是否覆盖 DPI；当前窗口会按屏幕大小限制初始尺寸。
- **控制台乱码？** 使用 `python -X utf8 ...`；CLI 主入口对无法编码的字符用替代输出。

本轮修复和验证记录见 `docs/REVIEW_2026-11-12.md`；先前审查记录保留在 `docs/CODE_REVIEW_CHANGELOG.md`。
