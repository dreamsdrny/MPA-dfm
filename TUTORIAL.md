# BRD-AI 使用教程

> Cadence Allegro PCB Layout Constraint 自动生成系统

---

## 0. 快速开始 (.exe 可执行文件)

### 0.1 下载即用

1. 将 `dist/BRD-AI/` 整个文件夹复制到任意位置
2. 双击 `BRD-AI.exe` 启动图形界面
3. 选择 BRD 文件、Checklist Excel 文件，点击 "Start Generation"

### 0.2 命令行模式

```powershell
# 启动 GUI 图形界面
BRD-AI.exe

# 命令行模式 (如果打包为 console 版本)
python main.py --gui            # 启动GUI
python main.py --demo           # 演示模式
python main.py --brd xxx.brd --checklist xxx.xlsx  # 指定文件
```

### 0.3 打包说明

```powershell
# 安装打包工具
pip install pyinstaller

# 一键打包 (Windows)
build.bat

# 或手动打包
python -m PyInstaller --clean --noconfirm --name="BRD-AI" --windowed ^
    --add-data="config/checklist_rules.yaml;config" ^
    --add-data="config/dfm_capability.yaml;config" ^
    --hidden-import=openpyxl --hidden-import=yaml --hidden-import=jinja2 ^
    gui.py
```

输出文件: `dist/BRD-AI/BRD-AI.exe` (~6.4MB)

---

## 1. 项目概述

BRD-AI 是一个自动化工具，用于将 **PCB Layout Checklist 规则** 和 **PCB制程能力** 自动转换为 **Cadence Allegro 可执行的 SKILL 约束脚本**。

### 核心能力

| 功能 | 说明 |
|------|------|
| 信号自动分类 | 根据Net名称自动识别 DDR/PCIe/USB/MIPI/Ethernet/RF/SPI/I2C等 |
| 差分对自动识别 | 自动匹配 `_P`/`_N` 信号对 |
| 约束规则计算 | 根据Checklist和信号类型计算Width/Spacing/Via规则 |
| DFM制程校验 | 对比PCB厂制程能力，自动报错 |
| SKILL脚本生成 | 生成可直接在Allegro中执行的约束脚本 |
| Excel报告 | 生成完整的约束规则报告 |

### 架构流程

```
.brd文件 / Net列表
      ↓
 信号自动分类 (signal_classifier)
      ↓
 规则计算引擎 (rule_engine)
      ↓
 DFM制程校验 (dfm_engine)
      ↓
 SKILL脚本生成 (skill_generator)  →  auto_constraint.il
      ↓
 报告生成 (report_generator)       →  constraint_report.xlsx
```

---

## 2. 环境准备

### 2.1 安装Python依赖

```powershell
pip install pyyaml openpyxl jinja2
```

### 2.2 项目结构

```
brd_ai/
├── main.py                     # 主入口
├── config/
│   ├── checklist_rules.yaml    # Checklist规则参数化
│   └── dfm_capability.yaml     # 制程能力数据库
├── src/
│   ├── pcb_reader.py           # BRD文件解析
│   ├── signal_classifier.py    # 信号自动分类
│   ├── rule_engine.py          # 规则计算引擎
│   ├── dfm_engine.py           # DFM制程能力校验
│   ├── skill_generator.py      # SKILL脚本生成器
│   └── report_generator.py     # 报告生成器
├── templates/
│   └── skill_template.j2       # SKILL模板 (Jinja2)
├── output/                     # 生成文件输出目录
│   ├── auto_constraint.il      # Allegro约束脚本
│   ├── constraint_report.xlsx  # Excel约束报告
│   ├── constraint_report.txt   # 文本约束报告
│   └── constraint_data.json    # JSON中间数据
└── data/                       # 中间数据
```

---

## 3. 快速开始

### 3.1 演示模式 (推荐首次使用)

```powershell
cd D:\Evan\BRD-AI\brd_ai
python main.py --demo
```

这会使用内置的 **PSE Board (860-000776)** 演示数据运行完整流程，生成:
- `output/auto_constraint.il` — Allegro SKILL 约束脚本
- `output/constraint_report.xlsx` — Excel 约束报告
- `output/constraint_report.txt` — 文本报告
- `output/constraint_data.json` — JSON 中间数据

### 3.2 使用真实BRD文件

```powershell
python main.py --brd "../860-000776_FAB_BRD_Rev_A_260721_3D.brd"
```

### 3.3 使用高级制程能力

```powershell
python main.py --brd "../860-000776_FAB_BRD_Rev_A_260721_3D.brd" --capability advanced
```

### 3.4 指定输出目录

```powershell
python main.py --demo --output ./my_custom_output
```

### 3.5 从JSON文件导入Net列表

```powershell
python main.py --nets ./my_nets.json
```

---

## 4. 在 Allegro 中执行约束脚本

### 方法1: Command窗口 (推荐)

1. 打开 Allegro PCB Editor
2. 打开你的 `.brd` 文件
3. 在 Command 窗口输入:

```lisp
skill load "D:/Evan/BRD-AI/brd_ai/output/auto_constraint.il"
```

### 方法2: Script Replay

1. Allegro 菜单: `File` → `Script` → `Replay`
2. 选择 `auto_constraint.il`
3. 点击 `Replay`

### 方法3: 启动时自动加载

在 `allegro.ilinit` 文件中添加:

```lisp
load("D:/Evan/BRD-AI/brd_ai/output/auto_constraint.il")
```

---

## 5. 配置文件说明

### 5.1 Checklist规则 (`config/checklist_rules.yaml`)

从 `Layout_Checklist_Rev1.0.xlsx` 提取的参数化规则，包含:

- **spacing**: 间距规则 (PTH-SMD, IC-IC, 0201/0402/0603间距等)
- **high_speed**: 高速信号规则 (3W, 5H, 差分阻抗容差等)
- **power**: 电源规则 (载流能力, 20H原则等)
- **board_edge**: 板边规则 (铜皮到板边间距等)
- **test_point**: 测试点规则 (直径, 间距等)
- **differential_impedance**: 差分阻抗预设 (按协议: USB3/DDR/PCIE/MIPI等)

### 5.2 制程能力 (`config/dfm_capability.yaml`)

两种级别:

| 参数 | Standard | Advanced |
|------|----------|----------|
| 最小线宽 | 0.075mm | 0.050mm |
| 最小间距 | 0.075mm | 0.050mm |
| 最小PTH | 0.20mm | 0.15mm |
| 最小孔环 | 0.05mm | 0.04mm |
| BGA最小间距 | 0.40mm | 0.35mm |

**自定义制程能力**: 直接编辑 `dfm_capability.yaml` 添加新的级别。

---

## 6. 信号分类规则

### 6.1 支持的信号类型

| 信号类型 | 识别模式 | Net Class |
|----------|----------|-----------|
| 电源 | `24V`, `3V3`, `VCC`, `VDD`, `VBUS` 等 | POWER |
| 地 | `GND`, `AGND`, `PGND`, `DGND` 等 | GND |
| 时钟 | `CLK`, `OSC`, `XTAL`, `CRYSTAL` 等 | CLOCK |
| 复位 | `RESET`, `RST`, `POR` 等 | RESET |
| DDR | `DDR`, `DQ`, `DQS`, `DM` 等 | DDR |
| PCIe | `PCIE`, `PCI_E` 等 | PCIE |
| USB3 | `USB3`, `SS_TX`, `SS_RX` 等 | USB3 |
| USB2 | `USB_DP`, `USB_DM`, `USB2` 等 | USB2 |
| MIPI | `MIPI`, `DSI`, `CSI` 等 | MIPI |
| LVDS | `LVDS` 等 | LVDS |
| Ethernet | `ETH`, `MDI`, `RGMII`, `SGMII` 等 | ETHERNET |
| SPI | `SPI`, `MISO`, `MOSI`, `SCLK` 等 | SPI |
| I2C | `I2C`, `SDA`, `SCL` 等 | I2C |
| UART | `UART`, `TX`, `RX`, `TXD`, `RXD` 等 | UART |
| CAN | `CAN`, `CANH`, `CANL` 等 | CAN |
| RF | `RF`, `ANT`, `ANTENNA`, `FEED` 等 | RF |
| Gate驱动 | `GATE`, `DRV`, `PWM`, `HSG`, `LSG` 等 | GATE_DRIVE |
| 检测 | `SEN`, `SENSE`, `ADC`, `VDET` 等 | SENSE |

### 6.2 差分对自动识别

自动匹配模式:
- `NET_P` + `NET_N` (如 `ETH_MDI0_P` + `ETH_MDI0_N`)
- `NET_H` + `NET_L`
- `NET_POS` + `NET_NEG`
- `NET_PLUS` + `NET_MINUS`

### 6.3 自定义信号分类

编辑 `src/signal_classifier.py` 中的 `SIGNAL_PATTERNS` 字典，添加新的信号类型和正则表达式。

---

## 7. 约束规则定制

### 7.1 修改线宽规则

编辑 `src/rule_engine.py` 中的 `DEFAULT_WIDTH_TABLE`:

```python
DEFAULT_WIDTH_TABLE = {
    "DDR": {"min": 0.08, "preferred": 0.10, "max": 0.12, "unit": "mm"},
    "POWER": {"min": 0.30, "preferred": 0.50, "max": 2.00, "unit": "mm"},
    # 添加自定义...
}
```

### 7.2 修改差分对配置

编辑 `src/rule_engine.py` 中的 `DIFF_PAIR_CONFIG`:

```python
DIFF_PAIR_CONFIG = {
    "DDR": {
        "min_line": 0.08, "pref_line": 0.10, "max_line": 0.12,
        "min_gap": 0.08, "pref_gap": 0.10, "max_gap": 0.15,
        "unit": "mm",
    },
    # 添加自定义...
}
```

### 7.3 修改差分阻抗目标

编辑 `config/checklist_rules.yaml` 中的 `differential_impedance`:

```yaml
differential_impedance:
  USB3:     { impedance: 90, tolerance: 15, unit: ohm }
  DDR:      { impedance: 100, tolerance: 10, unit: ohm }
  MyProtocol: { impedance: 85, tolerance: 10, unit: ohm }  # 新增
```

---

## 8. SKILL脚本验证

生成的SKILL脚本在Allegro中执行前，可以进行语法检查:

### 在Allegro中逐行验证:

```lisp
; 测试单个命令
(axlCNSEnter)
(axlCNSAddNetClass "POWER" '("24V" "3V3_MB"))
(axlCNSExit)
```

### 查看生成的约束:

```lisp
; 列出所有Net Class
(axlCNSGetNetClasses)

; 查看物理约束
(axlCNSGetPhysicalConstraints)

; 查看间距约束
(axlCNSGetSpacingConstraints)
```

---

## 9. 常见问题

### Q1: BRD文件解析不到Net?

A1: 当前 `pcb_reader.py` 使用二进制解析。如果解析不到，可以:
1. 在Allegro中导出Net列表: `File → Export → Netlist`
2. 使用 `--nets` 参数导入JSON格式的Net列表

### Q2: 如何添加新的Protocol?

A2: 三步:
1. `signal_classifier.py` 添加识别正则
2. `rule_engine.py` 添加 Width/Spacing/Via 规则
3. `checklist_rules.yaml` 添加阻抗预设

### Q3: DFM校验报错，但实际可生产?

A3: 调整 `config/dfm_capability.yaml` 中的制程能力参数，或使用 `--capability advanced`。

### Q4: 生成的SKILL脚本执行失败?

A4: 检查Allegro版本。不同版本的SKILL API可能有差异。建议在Allegro中逐步执行脚本中的命令。

---

## 10. 扩展开发

### 10.1 添加GUI界面

```python
# 使用 tkinter 或 PyQt 添加GUI
pip install PyQt5
```

### 10.2 连接Impedance Calculator

在 `rule_engine.py` 中添加基于Stackup的阻抗计算:

```python
def calculate_impedance_width(stackup, target_z=50):
    # 使用 Polar Si8000 公式或调用外部工具
    pass
```

### 10.3 集成AI规则引擎

```python
# 使用 LLM API 解析自然语言Checklist
def parse_checklist_with_ai(checklist_text):
    # 调用 OpenAI / 本地模型 API
    pass
```

---

## 11. 文件清单

| 文件 | 说明 |
|------|------|
| `main.py` | 主入口，整合所有模块 |
| `config/checklist_rules.yaml` | 从Checklist提取的规则参数 |
| `config/dfm_capability.yaml` | 制程能力数据库 |
| `src/pcb_reader.py` | BRD文件二进制解析 |
| `src/signal_classifier.py` | 信号名模式匹配分类 |
| `src/rule_engine.py` | 约束规则计算 |
| `src/dfm_engine.py` | DFM制程能力校验 |
| `src/skill_generator.py` | SKILL脚本生成 (Jinja2模板) |
| `src/report_generator.py` | Excel/文本报告生成 |
| `output/auto_constraint.il` | 生成的可执行SKILL脚本 |
| `output/constraint_report.xlsx` | 完整约束报告 |