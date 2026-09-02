# BRD-AI 使用教程

> Cadence Allegro PCB Layout Constraint 自动生成系统

BRD-AI 是一个自动化工具，用于将 **PCB Layout Checklist 规则** 与 **PCB 厂制程能力** 自动转换为 **Cadence Allegro 可直接执行的 SKILL 约束脚本**，并同步生成约束报告、DFM 制程能力校验报告与已填写的 Checklist。

---

## 目录

1. [项目概述](#1-项目概述)
2. [整体架构与处理流程](#2-整体架构与处理流程)
3. [环境准备](#3-环境准备)
4. [项目目录结构](#4-项目目录结构)
5. [快速开始](#5-快速开始)
6. [命令行参数完整参考](#6-命令行参数完整参考)
7. [图形界面 (GUI) 使用](#7-图形界面-gui-使用)
8. [DFM 功能分析模式](#8-dfm-功能分析模式)
9. [配置文件说明](#9-配置文件说明)
10. [信号自动分类规则](#10-信号自动分类规则)
11. [约束规则计算引擎](#11-约束规则计算引擎)
12. [DFM 制程能力校验引擎](#12-dfm-制程能力校验引擎)
13. [Checklist 自动填写](#13-checklist-自动填写)
14. [在 Allegro 中执行 SKILL 脚本](#14-在-allegro-中执行-skill-脚本)
15. [输出文件说明](#15-输出文件说明)
16. [自定义与扩展](#16-自定义与扩展)
17. [打包为可执行文件](#17-打包为可执行文件)
18. [常见问题 (FAQ)](#18-常见问题-faq)
19. [文件清单](#19-文件清单)

---

## 1. 项目概述

### 1.1 定位

BRD-AI 的目标是把 **"人工核对 Layout Checklist + 手工在 Allegro 里逐条设置约束"** 的重复劳动自动化。它按以下思路工作：

```
Excel Checklist 规则 / 制程能力数据库
        ↓
  Python 规则计算引擎
        ↓
  Cadence SKILL 脚本 (auto_constraint.il)
        ↓
  Allegro PCB Editor → Constraint Manager
```

核心价值：

| 能力 | 说明 |
|------|------|
| 信号自动分类 | 根据 Net 名称正则自动识别 DDR/PCIe/USB/MIPI/Ethernet/RF/SPI/I2C 等 |
| 差分对自动识别 | 自动匹配 `_P`/`_N`、`_H`/`_L`、`_POS`/`_NEG`、`_PLUS`/`_MINUS` 等信号对 |
| 约束规则计算 | 根据信号类型计算线宽 / 间距 / 过孔 / 阻抗约束 |
| DFM 制程校验 | 对比板厂制程能力，自动报错 / 警告 |
| 多板厂支持 | 内置江西红版、四会两家制程能力库 |
| 多板型支持 | 支持硬板 (rigid) 与软板/软硬结合板 (flex) |
| SKILL 脚本生成 | 生成可在 Allegro 中直接执行的约束脚本 |
| Excel / 文本报告 | 生成完整约束报告与 DFM 报告 |
| Checklist 自动填写 | 根据分析结果自动填写 Check / Approved 列 |

### 1.2 输入与输出

- **输入**：`.brd` 文件，或 Net 名称列表 JSON；可选的 `Layout Checklist` Excel 文件。
- **输出**：
  - `auto_constraint.il` — Allegro SKILL 约束脚本
  - `constraint_report.xlsx` — Excel 约束报告
  - `constraint_report.txt` — 文本约束报告
  - `constraint_data.json` — JSON 中间数据
  - `<checklist>_filled.xlsx` — 自动填写后的 Checklist
  - `dfm_analysis.json` — DFM 功能分析详细报告（`--dfm` 模式）

---

## 2. 整体架构与处理流程

### 2.1 模块划分

BRD-AI 采用模块化设计，各模块职责单一：

| 模块 | 文件 | 职责 |
|------|------|------|
| 主入口 | `main.py` | 命令行入口，串联整个流水线 |
| GUI | `gui.py` | tkinter 图形界面 |
| PCB 解析器 | `src/pcb_reader.py` | 从 `.brd` 二进制文件提取 Net / 层 / 器件 / 叠层 |
| 信号分类器 | `src/signal_classifier.py` | Net 名称模式匹配，映射到 Net Class 与协议 |
| 规则引擎 | `src/rule_engine.py` | 计算线宽 / 间距 / 过孔 / 阻抗约束 |
| DFM 引擎 | `src/dfm_engine.py` | 多板厂制程能力校验 |
| SKILL 生成器 | `src/skill_generator.py` | 将约束渲染为 SKILL 脚本 (Jinja2 模板) |
| 报告生成器 | `src/report_generator.py` | 生成 Excel / 文本报告 |
| Checklist 解析 | `src/checklist_reader.py` | 解析 Layout Checklist Excel，提取规则 |
| Checklist 填写 | `src/checklist_auto_fill.py` | 根据分析结果自动填写 Check / Approved 列 |

### 2.2 主流水线

`main.py` 的 `run_pipeline()` 依次执行以下步骤：

```
[1/6] 读取 PCB 数据        (pcb_reader / 内置演示数据)
[2/6] 信号自动分类          (signal_classifier)
[2.5] 读取 Checklist       (checklist_reader, 可选)
[3/6] 计算约束规则          (rule_engine)
[4/6] DFM 制程能力校验      (dfm_engine)
[5/6] 生成 SKILL 脚本       (skill_generator)
[6/6] 生成报告              (report_generator)
[7/7] 自动填写 Checklist    (checklist_auto_fill, 可选)
```

数据在模块间以 Python `dict` 结构传递，最终统一导出到 `output/` 目录。

---

## 3. 环境准备

### 3.1 Python 版本

推荐 **Python 3.10 及以上**（开发环境使用 3.12）。

### 3.2 安装依赖

```powershell
pip install pyyaml openpyxl jinja2
```

| 依赖 | 用途 |
|------|------|
| `pyyaml` | 读取 `config/*.yaml` 配置文件 |
| `openpyxl` | 读写 Excel 报告与 Checklist |
| `jinja2` | 渲染 SKILL 脚本模板 |

### 3.3 可选的打包依赖

如需打包成 `.exe`，再安装：

```powershell
pip install pyinstaller
```

---

## 4. 项目目录结构

```
brd_ai/
├── main.py                       # 命令行主入口
├── gui.py                        # tkinter 图形界面
├── build.bat                     # 一键打包脚本 (Windows)
├── BRD-AI.spec                   # PyInstaller 打包配置
├── dfm_design_sample.json        # DFM 设计参数示例
│
├── config/
│   ├── checklist_rules.yaml      # Checklist 规则参数化
│   ├── dfm_capability_hongban.yaml  # 江西红版制程能力库
│   └── dfm_capability_sihui.yaml    # 四会制程能力库
│
├── src/
│   ├── __init__.py
│   ├── pcb_reader.py             # BRD 文件二进制解析
│   ├── signal_classifier.py      # 信号自动分类
│   ├── rule_engine.py            # 约束规则计算引擎
│   ├── dfm_engine.py             # DFM 制程能力校验
│   ├── skill_generator.py        # SKILL 脚本生成器
│   ├── report_generator.py       # 报告生成器
│   ├── checklist_reader.py       # Checklist Excel 解析器
│   └── checklist_auto_fill.py    # Checklist 自动填写引擎
│
├── output/                       # 生成文件输出目录 (自动创建)
│   ├── auto_constraint.il        # Allegro 约束脚本
│   ├── constraint_report.xlsx    # Excel 约束报告
│   ├── constraint_report.txt     # 文本约束报告
│   ├── constraint_data.json      # JSON 中间数据
│   ├── dfm_analysis.json         # DFM 分析报告 (--dfm 模式)
│   └── *_filled.xlsx             # 已填写的 Checklist
│
└── dist/                         # PyInstaller 打包产物 (自动创建)
    └── BRD-AI/
        └── BRD-AI.exe
```

> 注意：`dfm_capability.yaml` 是旧版单板厂能力库文件名，当前代码已按工厂拆分为 `dfm_capability_hongban.yaml` 与 `dfm_capability_sihui.yaml`，`dfm_engine.load_capability_db()` 在找不到对应文件时会回退到 `dfm_capability.yaml`。

---

## 5. 快速开始

### 5.1 演示模式（推荐首次体验）

演示模式使用内置的 **PSE Board (860-000776)** 演示数据（82 个 Net），无需任何输入文件：

```powershell
cd D:\Evan\BRD-AI\brd_ai
python main.py --demo
```

运行结束后，`output/` 目录会生成 4 个文件：`auto_constraint.il`、`constraint_report.xlsx`、`constraint_report.txt`、`constraint_data.json`。

### 5.2 使用真实 BRD 文件

```powershell
python main.py --brd "../860-000776_FAB_BRD_Rev_A_260721_3D.brd"
```

### 5.3 使用 Net 列表 JSON（替代 BRD 文件）

当 `.brd` 二进制解析不完整时，可直接提供一个 Net 名称列表 JSON：

```json
["24V", "3V3_MB", "GND", "CLK_25MHZ", "SPI_MISO", "SPI_MOSI", "ETH_MDI0_P", "ETH_MDI0_N"]
```

```powershell
python main.py --nets ./my_nets.json
```

### 5.4 指定制程能力等级与板厂

```powershell
# 使用车规等级
python main.py --brd my_board.brd --capability automotive

# 使用四会板厂
python main.py --brd my_board.brd --factory sihui

# 软板 / 软硬结合板
python main.py --brd my_board.brd --board-type flex
```

### 5.5 指定输出目录

```powershell
python main.py --demo --output ./my_output
```

### 5.6 附带 Checklist 自动填写

```powershell
python main.py --brd my_board.brd --checklist "../Layout_Checklist_Rev1.0.xlsx"
```

生成后会在输出目录得到 `Layout_Checklist_Rev1.0_filled.xlsx`。

### 5.7 启动 GUI

```powershell
python main.py --gui
# 或直接
python gui.py
```

---

## 6. 命令行参数完整参考

`main.py` 支持以下参数：

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--brd` | str | None | Cadence Allegro `.brd` 文件路径 |
| `--demo` | flag | False | 使用内置演示数据 (PSE Board) |
| `--nets` | str | None | Net 名称列表 JSON 文件路径（替代 `--brd`） |
| `--capability` | str | standard | 制程能力等级：`standard`(常规) / `automotive`(车规) / `extreme`(极限)；`advanced` 为旧版兼容名（等价 extreme） |
| `--board-type` | str | rigid | 板类型：`rigid`(硬板) / `flex`(软板及软硬结合板) |
| `--factory` | str | hongban | 板厂制程能力库：`hongban`(红版) / `sihui`(四会) |
| `--copper` | float | 35.0 | 成品铜厚 (um)，用于线宽/线距能力分档判断 |
| `--copper-oz` | float | None | 成品铜厚 (oz)，用于阻焊桥能力分档判断（缺省按 `--copper` 推算） |
| `--output` | str | `output/` | 生成文件输出目录 |
| `--checklist` | str | None | Layout Checklist Excel 文件路径 (.xlsx) |
| `--autofill` | str | None | 自动填写 Checklist 的输出路径 |
| `--checker` | str | BRD-AI | 自动填写时写入 Approved 列的检查者名称 |
| `--gui` | flag | False | 启动图形界面 |
| `--dfm` | str | None | DFM 功能分析模式：传入设计参数 JSON 文件（或用 `template` 生成模板） |

### 6.1 使用示例

```powershell
# 演示模式
python main.py --demo

# 真实 BRD + 高级制程能力
python main.py --brd my_board.brd --capability extreme

# 指定板厂 + 软板 + 铜厚
python main.py --brd my_board.brd --board-type flex --factory sihui --copper 35

# 指定输出目录
python main.py --brd my_board.brd --output ./out

# 从 JSON 导入 Net 列表
python main.py --nets ./nets.json --capability standard

# 附带 Checklist 自动填写
python main.py --brd my_board.brd --checklist ./Layout_Checklist.xlsx --checker "张三"

# 生成 DFM 设计参数模板
python main.py --dfm template

# 执行 DFM 功能分析
python main.py --dfm ./dfm_design_sample.json --board-type rigid --capability standard --factory hongban
```

### 6.2 注意事项

- `--brd` 与 `--nets` **互斥**，不能同时指定。
- 若未指定任何输入（`--brd` / `--nets` / `--demo`），默认自动进入 `--demo` 模式。
- `--dfm` 模式**不生成约束脚本**，只输出制程能力比对报告。

---

## 7. 图形界面 (GUI) 使用

### 7.1 启动

```powershell
python gui.py
# 或
python main.py --gui
```

### 7.2 界面组成

界面从上到下包含：

1. **Input Files（输入文件）**
   - `BRD File`：选择 `.brd` 文件
   - `Checklist Excel`：选择 Layout Checklist
   - `Output Dir`：输出目录（默认为 `output/`）

2. **Options（选项）**
   - `Factory`：板厂（hongban / sihui）
   - `Board Type`：板类型（rigid / flex）
   - `Capability`：能力等级（随工厂与板类型联动，自动列出可用等级）
   - `Copper(um)` / `(oz)`：成品铜厚
   - `Auto-fill Checklist`：勾选后启用 Checklist 自动填写
   - `DFM 功能分析`：打开独立的 DFM 功能分析对话框
   - `Checker Name`：填写 Approved 列的检查者名称
   - `Net List (JSON)`：可选，用 JSON 代替 BRD 文件

3. **Buttons（按钮）**
   - `Start Generation`：开始生成（需选择 BRD 或 Net List JSON）
   - `Demo Run`：运行演示数据
   - `Open Output Folder`：打开输出目录

4. **Progress Bar / Log / Status Bar**：进度条、彩色日志、状态栏

### 7.3 运行流程

点击 `Start Generation` 后，GUI 在后台线程执行与命令行相同的 6（或 7）步流水线，日志实时输出到 Log 区域，结束后状态栏显示 `Done!`。

### 7.4 DFM 功能分析对话框

点击 `DFM 功能分析` 按钮打开对话框，可直接输入设计参数，选择板厂 / 板类型 / 能力等级 / 铜厚，点击 `执行分析` 后以表格形式显示 PASS / WARN / FAIL / INFO 结果。

设计参数字段（留空则跳过该检查项）：

| 类别 | 字段 | 说明 |
|------|------|------|
| 拼板/板尺寸 | 板尺寸 X / Y (mm) | 板子外形尺寸 |
| 线路 | 最小线宽 / 最小线距 (mm) | |
| 线路 | 成品铜厚 (um) | |
| 线路 | 不同网络孔壁间距 (mm) | 涉及 CAF |
| 线路 | 内层/外层线路到板边 (mm) | |
| 钻孔 | 最小/最大机械钻孔 (mm) | |
| 钻孔 | 最小激光孔 (mm) | |
| 钻孔 | 单边孔环 (mm) | |
| 钻孔 | 板厚 (mm) | |
| 钻孔 | NPTH 孔到板边 (mm) | |
| 阻焊 | 阻焊桥 (mm) / 阻焊开窗 (mm) | |
| 塞孔 | 塞孔孔径 (mm) | |
| 丝印 | 丝印线宽 / 丝印间隙 (mm) | |
| 覆盖膜(软板) | 覆盖膜开窗间距 / 到焊盘 / 到线路 (mm) | 仅软板 |
| 测试 | 电测 PAD 二线 (mm) | |

---

## 8. DFM 功能分析模式

DFM 功能分析模式（`--dfm`）用于**不生成约束脚本**的情况下，仅将设计参数与所选板厂制程能力逐项比对。

### 8.1 生成设计参数模板

```powershell
python main.py --dfm template
```

会打印一份 JSON 模板，包含全部可选设计参数：

```json
{
  "board_size": [100, 80],
  "min_line_width": 0.10, "min_spacing": 0.10,
  "copper_um": 35, "copper_oz": 1.0,
  "min_mech_drill": 0.2, "max_mech_drill": 3.2,
  "min_laser_via": 0.1, "min_annular_ring": 0.075,
  "board_thickness": 1.6,
  "pth_to_pth_spacing": 0.3,
  "copper_to_edge_inner": 0.25, "copper_to_edge_outer": 0.2,
  "npth_to_edge": 0.5,
  "solder_mask_bridge": 0.1, "solder_mask_opening": 0.1,
  "silkscreen_width": 0.15, "silkscreen_clearance": 0.15,
  "plug_hole_diameter": 0.3, "plug_board_thickness": 1.6,
  "test_pad_2wire": 0.4,
  "coverlay_spacing": 0.5, "coverlay_to_pad": 0.3,
  "coverlay_to_trace": 0.15, "coverlay_window": [0.6, 0.6]
}
```

### 8.2 执行分析

```powershell
python main.py --dfm ./my_design.json --board-type rigid --capability standard --factory hongban
```

控制台会打印每一项的 PASS / WARN / FAIL / INFO 结果，并将完整结果写入 `output/dfm_analysis.json`。

### 8.3 支持的完整设计参数

`dfm_engine.analyze_design()` 支持以下参数（长度类单位 mm，铜厚 um，均可选）：

- `board_size`：板尺寸 `[x, y]` (mm)
- `min_line_width` / `min_spacing`：最小线宽 / 线距 (mm)
- `copper_um` / `copper_oz`：成品铜厚
- `min_mech_drill` / `max_mech_drill`：机械钻孔 (mm)
- `min_laser_via`：激光孔 (mm)
- `min_annular_ring`：单边孔环 (mm)
- `board_thickness`：板厚 (mm)
- `min_drill_for_ar`：计算纵横比用最小孔径（缺省取 `min_mech_drill`）
- `pth_to_pth_spacing`：不同网络孔壁间距 (mm)
- `copper_to_edge_inner` / `copper_to_edge_outer`：线路到板边 (mm)
- `npth_to_edge`：NPTH 孔壁到板边 (mm)
- `solder_mask_bridge` / `solder_mask_opening`：阻焊桥 / 开窗 (mm)
- `silkscreen_width` / `silkscreen_clearance`：丝印线宽 / 间隙 (mm)
- `silkscreen_char_width` / `silkscreen_char_height`：丝印字符 (mm)
- `plug_hole_diameter` / `plug_board_thickness`：塞孔孔径 / 板厚 (mm)
- `coverlay_window` / `coverlay_spacing` / `coverlay_to_pad` / `coverlay_to_trace`：覆盖膜参数 (仅软板)
- `test_pad_2wire` / `test_pad_4wire`：电测 PAD (mm)

### 8.4 判定逻辑

- **PASS**：设计值满足制程能力。
- **WARN (warn_limit)**：设计值接近制程极限（如 `>=` 检查中设计值在能力的 80%~100% 区间）。
- **FAIL**：设计值超出制程能力。
- **INFO**：该等级无对应制程数据，需与板厂评估。

---

## 9. 配置文件说明

### 9.1 Checklist 规则 (`config/checklist_rules.yaml`)

从 `Layout_Checklist_Rev1.0.xlsx` 提取的参数化规则，主要包含：

| 分类 | 内容 |
|------|------|
| `spacing` | 间距规则（PTH-SMD、IC-IC、0201/0402/0603 间距等） |
| `high_speed` | 高速规则（3W、5H、蛇形线、差分阻抗容差等） |
| `power` | 电源规则（载流能力、20H 原则等） |
| `board_edge` | 板边规则（铜皮到板边、安装孔间距等） |
| `test_point` | 测试点规则（直径、间距等） |
| `silkscreen` | 丝印规则 |
| `solder_mask` | 阻焊规则 |
| `differential_impedance` | 差分阻抗预设（按协议） |

差分阻抗预设示例：

```yaml
differential_impedance:
  USB3:     { impedance: 90, tolerance: 15, unit: ohm }
  DDR:      { impedance: 100, tolerance: 10, unit: ohm }
  PCIE:     { impedance: 85, tolerance: 15, unit: ohm }
  MIPI:     { impedance: 100, tolerance: 10, unit: ohm }
  LVDS:     { impedance: 100, tolerance: 10, unit: ohm }
  Ethernet: { impedance: 100, tolerance: 10, unit: ohm }
  RF:       { impedance: 50, tolerance: 5, unit: ohm }
  default:  { impedance: 100, tolerance: 10, unit: ohm }
```

### 9.2 制程能力库 (`config/dfm_capability_*.yaml`)

两个板厂的能力库结构一致，均包含 `board_types`：

- **`rigid`（硬板）**：等级 `standard` / `automotive` / `extreme`
- **`flex`（软板及软硬结合板）**：红版为 `standard` / `extreme`，四会为 `standard` / `automotive` / `extreme`

每个等级包含的制程能力分类：

| 分类 | 说明 |
|------|------|
| `panel` | 拼板（最大拼板尺寸、阵列、最小拼板数、工艺边） |
| `trace` | 线路（按铜厚分档的最小线宽/线距、线宽公差、孔壁间距、到板边） |
| `lamination` | 压合（介质厚度、层间对位） |
| `drill` | 钻孔（机械/激光孔、孔环、孔公差、孔壁粗糙度） |
| `plating` | 电镀（盲孔填充、镀铜均匀性、PTH 纵横比） |
| `plug_hole` | 塞孔（树脂/油墨塞孔条件） |
| `solder_mask` | 阻焊（油墨厚度、阻焊桥、开窗） |
| `silkscreen` | 丝印 |
| `surface_finish` | 表面处理（ENIG/OSP/硬金/ENEPIG 等） |
| `coverlay` | 覆盖膜（仅软板） |
| `routing` / `stiffener` | 锣型 / 补强（仅软板） |
| `test` | 电测 PAD |

#### 9.2.1 单位约定

- 每项均带 `unit` 字段（`mm` / `um` / `mil` / `percent` / `ratio`）。
- mil → mm 换算：`1 mil = 0.0254 mm`。
- 铜厚表中 `T` 为成品铜厚 (um)。
- 线宽/线距 L/S 表：分档区间 `copper_min_um` ~ `copper_max_um`，对应 `line_um` / `space_um`。

#### 9.2.2 自定义制程能力

直接编辑对应板厂的 YAML，或在 `config/` 下新增文件并在 `dfm_engine.FACTORY_FILES` 中注册新板厂：

```python
FACTORY_FILES = {
    "hongban": "dfm_capability_hongban.yaml",
    "sihui":   "dfm_capability_sihui.yaml",
    "myfab":   "dfm_capability_myfab.yaml",   # 新增板厂
}
FACTORY_LABELS = {
    "myfab": "某某板厂",
}
```

---

## 10. 信号自动分类规则

### 10.1 支持的信号类型

`src/signal_classifier.py` 中的 `SIGNAL_PATTERNS` 定义了 20 类信号（另有未匹配时的 `DEFAULT` 兜底类），核心规则如下：

| 信号类型 | 识别模式 (正则前缀示例) | width_rule | spacing_rule | 差分/阻抗 |
|----------|------------------------|-----------|-------------|-----------|
| POWER | `24V`/`3V3`/`VCC`/`VDD`/`VBUS`/`_PWR` 等 | POWER | POWER | — |
| GND | `GND`/`AGND`/`PGND`/`GND1` 等 | GND | GND | — |
| CLOCK | `CLK`/`OSC`/`XTAL`/`CRYSTAL` | CLOCK | CLOCK_3W | — |
| RESET | `RESET`/`RST`/`POR`/`_N`/`_B` | RESET | RESET_5W | — |
| DDR | `DDR`/`DQ`/`DQS`/`DM`/`_CK_P` 等 | DDR | DDR_3W | 差分，100Ω |
| PCIE | `PCIE`/`PCI_E` | PCIE | PCIE_3W | 差分，85Ω |
| USB3 | `USB3`/`SS_TX`/`SS_RX` | USB3 | USB3 | 差分，90Ω |
| USB2 | `USB_DP`/`USB_DM`/`USB2` | USB2 | USB2 | 差分，90Ω |
| MIPI | `MIPI`/`DSI`/`CSI`/`_D0P` 等 | MIPI | MIPI_3W | 差分，100Ω |
| LVDS | `LVDS` | LVDS | LVDS_5H | 差分，100Ω |
| ETHERNET | `ETH`/`MDI`/`RGMII`/`SGMII` 等 | ETHERNET | ETHERNET | 差分，100Ω |
| SPI | `SPI`/`MISO`/`MOSI`/`SCLK` 等 | SPI | SPI_3W | — |
| I2C | `I2C`/`SDA`/`SCL` | I2C | default | — |
| UART | `UART`/`TX`/`RX`/`TXD`/`RXD` | default | default | — |
| CAN | `CAN`/`CANH`/`CANL` | CAN | CAN | 差分，120Ω |
| RF | `RF`/`ANT`/`FEED` 等 | RF | RF | 单端，50Ω |
| GATE_DRIVE | `GATE`/`DRV`/`PWM`/`HSG`/`LSG` 等 | GATE_DRIVE | GATE_DRIVE | — |
| SENSE | `SEN`/`SENSE`/`ADC`/`VDET` 等 | SENSE | SENSE | — |
| LED | `LED` | default | default | — |
| JTAG | `JTAG`/`TMS`/`TCK`/`TDI` 等 | default | default | — |
| DEFAULT | 未匹配到任何规则 | default | default | — |

### 10.2 分类优先级

一个 Net 名称可能同时匹配多个规则，系统按固定优先级取**最具体、优先级最高**的匹配：

```python
priority_order = [
    "GATE_DRIVE", "RF", "CAN", "MIPI", "LVDS", "PCIE",
    "USB3", "USB2", "ETHERNET", "DDR", "CLOCK", "RESET",
    "SPI", "I2C", "UART", "JTAG", "SENSE", "LED",
    "POWER", "GND",
]
```

### 10.3 差分对自动识别

`find_diff_pairs()` 通过后缀正则自动配对：

| 正端后缀 | 负端后缀 |
|----------|----------|
| `_P` | `_N` |
| `_H` | `_L` |
| `_POS` | `_NEG` |
| `_PLUS` | `_MINUS` |

例如 `ETH_MDI0_P` + `ETH_MDI0_N` 会被识别为一对差分对。

### 10.4 自定义信号分类

编辑 `src/signal_classifier.py` 的 `SIGNAL_PATTERNS`，添加新类型及正则：

```python
"MY_PROTOCOL": {
    "patterns": [r"^(MYPROTO|MP_|_MP)"],
    "width_rule": "DEFAULT",
    "spacing_rule": "default",
    "protocol": "MY_PROTOCOL",
    "is_diff_pair": True,
    "impedance_target": 100.0,
    "match_tolerance": "0.5mm",
},
```

同时在 `priority_order` 中插入该类型以控制优先级。

---

## 11. 约束规则计算引擎

`src/rule_engine.py` 中定义了四张默认规则表，作为约束计算的基线：

### 11.1 线宽表 (`DEFAULT_WIDTH_TABLE`)

| 信号类型 | min | preferred | max (mm) |
|----------|-----|-----------|----------|
| POWER / GND | 0.30 | 0.50 | 2.00 |
| CLOCK / RESET | 0.10 | 0.15 | 0.20 |
| DDR / PCIE | 0.08 | 0.10 | 0.12 |
| USB3 | 0.09 | 0.10 | 0.12 |
| USB2 | 0.10 | 0.15 | 0.20 |
| MIPI | 0.08 | 0.09 | 0.10 |
| LVDS | 0.08 | 0.10 | 0.12 |
| ETHERNET | 0.09 | 0.10 | 0.12 |
| SPI / I2C / UART / CAN | 0.10 | 0.15 | 0.20 |
| RF | 0.15 | 0.18 | 0.25 |
| GATE_DRIVE | 0.30 | 0.50 | 1.00 |
| SENSE / LED / JTAG / DEFAULT | 0.10 | 0.15 | 0.20 |

### 11.2 间距表 (`DEFAULT_SPACING_TABLE`)

| 规则名 | line_to_line | line_to_pin | line_to_via |
|--------|-------------|-------------|-------------|
| POWER / GND | 0.30 | 0.30 | 0.25 |
| CLOCK_3W | "3W" | 0.30 | 0.25 |
| RESET_5W | "5W" | 0.30 | 0.25 |
| DDR_3W / PCIE_3W | "3W" | 0.20 | 0.20 |
| USB3 / USB2 | 0.15 | 0.15 | 0.15 |
| MIPI_3W | "3W" | 0.15 | 0.15 |
| LVDS_5H | "5H" | 0.20 | 0.20 |
| ETHERNET / CAN | 0.20 | 0.20 | 0.20 |
| SPI_3W | "3W" | 0.15 | 0.15 |
| RF | 0.30 | 0.30 | 0.25 |
| GATE_DRIVE | 0.50 | 0.50 | 0.50 |
| SENSE | 0.20 | 0.15 | 0.15 |
| default | 0.15 | 0.15 | 0.15 |

> 注意：`"3W"` / `"5H"` 等字符串表示按走线宽度或叠层高度倍数取值，不会参与数值型 DFM 校验。

### 11.3 过孔表 (`DEFAULT_VIA_TABLE`)

| 信号类型 | pad | drill (mm) |
|----------|-----|-----------|
| POWER / GND / RF | 0.50 | 0.25 |
| GATE_DRIVE | 0.60 | 0.30 |
| 其余多数类型 | 0.40 | 0.20 |

### 11.4 差分对配置 (`DIFF_PAIR_CONFIG`)

对 DDR/PCIE/USB3/USB2/MIPI/LVDS/ETHERNET/CAN 定义了差分对的线宽与线距范围（min/pref/max）。

### 11.5 约束输出结构

`compute_class_constraints()` 为每个 Net Class 生成：

- **物理约束** `PHYS_<CLASS>`：线宽 min / preferred / max
- **间距约束** `SPC_<CLASS>`：line-to-line / line-to-pin / line-to-via
- **过孔约束** `VIA_<CLASS>`：pad / drill
- **电气约束** `ELEC_<CLASS>`：差分对阻抗目标、容差、线宽、线距

### 11.6 自定义规则

编辑 `src/rule_engine.py` 中的 `DEFAULT_WIDTH_TABLE`、`DEFAULT_SPACING_TABLE`、`DEFAULT_VIA_TABLE`、`DIFF_PAIR_CONFIG` 即可调整或新增规则。

---

## 12. DFM 制程能力校验引擎

`src/dfm_engine.py` 提供两类接口：

1. `validate_constraints()`：对已生成的**约束规则**做制程能力校验（旧接口兼容，主流水线使用）。
2. `analyze_design()`：对**设计参数**做制程能力比对（DFM 功能分析模式）。

### 12.1 主流水线 DFM 校验逻辑

`validate_constraints()` 会逐项检查：

| 检查项 | 规则 |
|--------|------|
| 最小线宽 | 物理约束 `width_min` < 制程能力 → 错误；< 能力的 1.2 倍 → 警告 |
| 最小间距 | 间距约束各值 < 制程能力 → 错误 |
| 过孔钻孔 | `drill` < 最小机械钻孔 → 错误 |
| 孔环 | `(pad - drill) / 2` < 单边孔环能力 → 错误 |
| 纵横比 | 板厚/孔径 > PTH 纵横比能力 → 错误 |
| 板边间距 | 低于建议值 → 警告 |
| 阻焊桥 | 低于制程能力 → 警告 |
| 丝印线宽 | 低于制程能力 → 警告 |
| 孔壁间距 | 低于制程能力 (CAF 风险) → 警告 |
| 差分对 | 差分线宽/线距低于制程能力 → 错误 |

### 12.2 按铜厚分档

最小线宽/线距能力按成品铜厚 `T` 分档查询。以红版硬板常规为例：

| 成品铜厚范围 (um) | 最小线宽/线距 (um) |
|-------------------|-------------------|
| T ≤ 25 | 60 / 60 |
| 25 < T ≤ 35 | 70 / 70 |
| 35 < T ≤ 52 | 100 / 100 |
| 52 < T ≤ 70 | 127 / 127 |
| 70 < T ≤ 87 | 140 / 140 |
| 87 < T ≤ 105 | 178 / 152 |

### 12.3 制程能力等级

| 板类型 | 等级 | 说明 |
|--------|------|------|
| rigid | standard | 常规 |
| rigid | automotive | 常规（车规，孔壁间距等加严） |
| rigid | extreme | 极限 |
| flex | standard | 常规 |
| flex | automotive | 常规（车规，仅四会） |
| flex | extreme | 极限 |

> 旧版 `advanced` 等级会自动映射为 `extreme`。

### 12.4 制程能力汇总

校验结果中 `capability_summary` 包含实际使用的能力值（最小线宽/线距、最小钻孔、孔环、纵横比、阻焊桥、丝印等），方便对照排查。

---

## 13. Checklist 自动填写

当指定 `--checklist`（或 GUI 勾选 Auto-fill）时，`src/checklist_auto_fill.py` 会读取 Layout Checklist，根据 BRD 分析结果自动填写 **Check 列（第 8 列）** 与 **Approved 列（第 9 列）**。

### 13.1 填写规则

| 结果 | Check 列 | 填充色 | 含义 |
|------|---------|--------|------|
| `PASS` | PASS | 绿色 | 自动验证通过 |
| `VERIFY` | VERIFY | 黄色 | 需在 Allegro 中运行 DRC 确认 |
| `MANUAL` | MANUAL | 蓝色 | 需人工目视/手动检查 |
| `N/A` | N/A | 灰色 | 不适用 |

Approved 列填写的格式为：`<检查者名称> / <时间戳>`。

### 13.2 判定机制

- **`VERIFIABLE_RULES`**：预定义的规则项（如 #37 约束管理器设置、#38 间距规则、#42 差分阻抗、#48 3W 原则等），判定为自动通过。
- **`MANUAL_REQUIRED`**：需人工确认的项（多数检查项）。
- **标准值为 `OK`**：判定为 PASS；标准值为 `NA`：判定为 N/A；其余：MANUAL。

### 13.3 汇总信息

自动填写完成后会在 Checklist 中追加（或更新）`summary` 工作表，记录：

```
<时间戳>  Auto  <检查者>  Auto-fill: X PASS / Y MANUAL / Z NA / T TOTAL
```

控制台同时输出自动完成率：

```
-> Auto PASS: 30, Manual: 105, N/A: 0
-> Auto Rate: 22.2%
```

---

## 14. 在 Allegro 中执行 SKILL 脚本

生成的 `auto_constraint.il` 通过 `axlCNS*` 系列 SKILL API 操作 Constraint Manager。

### 方法 1：Command 窗口（推荐）

1. 打开 Allegro PCB Editor，载入 `.brd` 文件。
2. 在 Command 窗口输入：

```lisp
skill load "D:/Evan/BRD-AI/brd_ai/output/auto_constraint.il"
```

### 方法 2：Script Replay

1. 菜单：`File` → `Script` → `Replay`。
2. 选择 `auto_constraint.il`。
3. 点击 `Replay`。

### 方法 3：启动时自动加载

在 `allegro.ilinit` 文件中添加：

```lisp
load("D:/Evan/BRD-AI/brd_ai/output/auto_constraint.il")
```

### 14.1 脚本主要 SKILL 调用

生成脚本会用到以下 API（不同 Allegro 版本 API 可能有差异）：

```lisp
(axlCNSEnter)                                    ; 进入 Constraint Manager
(axlCNSAddNetClass "POWER" '("24V" "3V3_MB"))     ; 创建 Net Class
(axlCNSAddPhysicalConstraint ...)                 ; 创建物理约束
(axlCNSSetPreferredWidth ...)                     ; 设置首选线宽
(axlCNSAddSpacingConstraint ...)                  ; 创建间距约束
(axlCNSAddViaConstraint ...)                      ; 创建过孔约束
(axlCNSAddDiffPair ...)                           ; 创建差分对
(axlCNSAddElectricalConstraint ...)               ; 创建电气约束
(axlCNSSetDiffPairImpedanceTolerance ...)         ; 设置差分阻抗容差
(axlCNSSetNetClassConstraint ...)                 ; 将约束绑定到 Net Class
(axlCNSExit)                                      ; 退出 Constraint Manager
```

### 14.2 在 Allegro 中逐行验证

```lisp
; 测试单个命令
(axlCNSEnter)
(axlCNSAddNetClass "POWER" '("24V" "3V3_MB"))
(axlCNSExit)

; 查看约束
(axlCNSGetNetClasses)             ; 列出所有 Net Class
(axlCNSGetPhysicalConstraints)    ; 查看物理约束
(axlCNSGetSpacingConstraints)     ; 查看间距约束
```

---

## 15. 输出文件说明

### 15.1 `auto_constraint.il`

可执行的 SKILL 约束脚本。开头包含生成时间、板名、能力等级等信息；主体定义了 `BRD_AI_CreateAllConstraints` 函数并在末尾调用执行。

### 15.2 `constraint_report.xlsx`

包含 7 个工作表：

| 工作表 | 内容 |
|--------|------|
| Summary | 板名、Net 数、层数、各类约束数量、DFM 结果汇总 |
| Net Classes | 每个 Net Class 的 Net 数、协议、是否差分/电源/地、规则名 |
| Physical Constraints | 各物理约束的线宽范围 |
| Spacing Constraints | 各间距约束的值 |
| Electrical Constraints | 各电气约束的阻抗/容差/线宽/线距 |
| DFM Report | DFM 校验的错误与警告 |
| Checklist Rules | 参数化的 Checklist 规则明细 |

### 15.3 `constraint_report.txt`

文本版约束报告，含 Net 数、各类约束数量、DFM 结果、Net Class 汇总。

### 15.4 `constraint_data.json`

结构化中间数据（板信息、分类结果、约束、DFM 结果），供二次处理或 AI 输入。

### 15.5 `dfm_analysis.json`

DFM 功能分析模式的详细结果（逐项 PASS/WARN/FAIL/INFO）。

### 15.6 `<checklist>_filled.xlsx`

自动填写后的 Checklist，Check / Approved 列已填，并带颜色标注。

---

## 16. 自定义与扩展

### 16.1 添加新 Protocol

三步完成：

1. `src/signal_classifier.py` — `SIGNAL_PATTERNS` 添加识别正则，并在 `priority_order` 注册。
2. `src/rule_engine.py` — 添加对应的 Width / Spacing / Via / Diff Pair 规则。
3. `config/checklist_rules.yaml` — 在 `differential_impedance` 添加阻抗预设。

### 16.2 添加新板厂

1. 在 `config/` 新增 `dfm_capability_<name>.yaml`。
2. 在 `src/dfm_engine.py` 的 `FACTORY_FILES` 与 `FACTORY_LABELS` 注册。
3. 在 `main.py` 的 `--factory` 参数 `choices` 中补充。

### 16.3 集成阻抗计算器

在 `rule_engine.py` 中加入基于叠层的阻抗计算：

```python
def calculate_impedance_width(stackup, target_z=50):
    # 使用 Polar Si8000 公式或调用外部工具
    pass
```

### 16.4 集成 AI 规则引擎

```python
def parse_checklist_with_ai(checklist_text):
    # 调用 LLM API 解析自然语言 Checklist
    pass
```

---

## 17. 打包为可执行文件

### 17.1 一键打包 (Windows)

```powershell
cd D:\Evan\BRD-AI\brd_ai
build.bat
```

`build.bat` 会自动检查并安装 PyInstaller，然后调用：

```powershell
python -m PyInstaller --clean --noconfirm --name="BRD-AI" --windowed ^
    --add-data="config/checklist_rules.yaml;config" ^
    --add-data="config/dfm_capability.yaml;config" ^
    --hidden-import=openpyxl --hidden-import=yaml --hidden-import=jinja2 ^
    gui.py
```

输出：`dist/BRD-AI/BRD-AI.exe`。

### 17.2 使用 spec 文件打包

项目提供了 `BRD-AI.spec` 与 `Layout-Brd-check-08-28-1.spec`。较新的 spec 会把两个板厂能力库都打包进去：

```powershell
python -m PyInstaller --clean --noconfirm Layout-Brd-check-08-28-1.spec
```

> 注意：若使用 `build.bat`，请确保它把 `dfm_capability_hongban.yaml` 与 `dfm_capability_sihui.yaml` 一并 `--add-data`（`dfm_engine` 会按文件名加载）。建议直接使用 spec 文件打包，确保所有配置随包分发。

### 17.3 运行

将 `dist/BRD-AI/`（或 `dist/Layout-Brd-check-08-28-1/`）整个文件夹复制到任意位置，双击其中的 `.exe` 即可启动 GUI。

---

## 18. 常见问题 (FAQ)

### Q1：BRD 文件解析不到 Net？

当前 `pcb_reader.py` 使用二进制正则解析。若解析不到，可：

1. 在 Allegro 中导出 Netlist：`File → Export → Netlist`。
2. 使用 `--nets` 参数导入 JSON 格式 Net 列表。

### Q2：如何添加新的 Protocol？

见 [16.1 添加新 Protocol](#161-添加新-protocol)。

### Q3：DFM 校验报错，但实际可生产？

1. 调整 `config/dfm_capability_*.yaml` 中的制程能力参数。
2. 切换能力等级：`--capability extreme`。
3. 确认 `--copper` 铜厚参数与实际成品铜厚一致（影响线宽/线距分档）。

### Q4：生成的 SKILL 脚本执行失败？

1. 检查 Allegro 版本，不同版本 SKILL API 可能有差异。
2. 建议在 Allegro 中逐步执行脚本中的命令排查。
3. 确认生成的 Net 名称与当前 BRD 中一致。

### Q5：`--factory` 或 `--capability` 的值如何确定？

- 板厂：`hongban`（红版）、`sihui`（四会）。
- 能力等级：用 `--capability` 时，若所选板厂/板类型不支持该等级，会自动回退到该板厂支持的默认等级；建议先查看对应 `dfm_capability_*.yaml` 的 `levels` 字段。

### Q6：flex 软板为什么多了覆盖膜/补强等检查项？

这些项在能力库中以 `coverlay` / `stiffener` / `routing` 分类定义，`analyze_design()` 仅在 `board_type == "flex"` 时执行覆盖膜相关检查。

---

## 19. 文件清单

| 文件 | 说明 |
|------|------|
| `main.py` | 主入口，整合所有模块 |
| `gui.py` | tkinter 图形界面 |
| `build.bat` | 一键打包脚本 |
| `config/checklist_rules.yaml` | Checklist 规则参数 |
| `config/dfm_capability_hongban.yaml` | 江西红版制程能力库 |
| `config/dfm_capability_sihui.yaml` | 四会制程能力库 |
| `src/pcb_reader.py` | BRD 文件二进制解析 |
| `src/signal_classifier.py` | 信号名模式匹配分类 |
| `src/rule_engine.py` | 约束规则计算 |
| `src/dfm_engine.py` | DFM 制程能力校验 |
| `src/skill_generator.py` | SKILL 脚本生成 (Jinja2 模板) |
| `src/report_generator.py` | Excel / 文本报告生成 |
| `src/checklist_reader.py` | Checklist Excel 解析 |
| `src/checklist_auto_fill.py` | Checklist 自动填写 |
| `dfm_design_sample.json` | DFM 设计参数示例 |
| `output/auto_constraint.il` | 生成的可执行 SKILL 脚本 |
| `output/constraint_report.xlsx` | 完整约束报告 |
| `output/constraint_report.txt` | 文本约束报告 |
| `output/constraint_data.json` | JSON 中间数据 |
| `output/dfm_analysis.json` | DFM 分析报告 |
