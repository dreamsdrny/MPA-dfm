# Evan_BRD 使用教程

## 统一工作台（新增）

运行 `python mpa_dfm.py`，或构建后打开 `dist/MPA_DFM.exe`。
主窗口包含 **PCB 厂制造能力分析** 和 **SMT 厂工艺能力分析** 两个页签；
页眉的 Checklist 按钮在同一进程中打开原有填写功能。
`build_evan_brd.bat` 现只构建一个 `MPA_DFM.exe`；下文双程序说明属于历史入口。

### 三个报告按钮与后台任务

窗口顶部的报告栏提供三个独立按钮：

| 按钮 | 前置结果 | 输出 |
|---|---|---|
| DFM 综合报告 | 当前输入的 PCB 和 SMT 分析均完成 | 一个 Excel（DFM总览、待办汇总、PCB与SMT完整分区）、TXT、JSON |
| PCB 报告 | PCB 分析完成 | PCB Excel、TXT |
| SMT 报告 | SMT 分析完成 | SMT Excel、JSON |

综合报告保留 PCB 问题明细与链接、逐项能力、数据来源，以及 SMT 配置版本、
限制值、裕量、来源和改善建议。缺数据或存在不符合不会因合并被变成通过。
PCB/SMT 分析、SMT YAML 读取及三类报告导出在后台工作线程执行；进度条显示后台任务。
计算过程中修改输入，会丢弃旧结果；导出使用点击时的结果快照，之后的修改不影响该份报告。
PCB 和 SMT 结果表分批渲染，降低大结果表对界面的阻塞。

### SMT YAML 导入

1. 以 `config/smt_capability_template.yaml` 为格式模板填写厂商能力。
2. 在 SMT 页签点「导入 SMT YAML」，选择产线/工艺配置。
3. 空白设计输入会引用 PCB 页签中已确认且同名的参数；也可明确输入人工确认值。
4. `scope: process` 的字段必须输入实际过程测量值，不从设计值推算。
5. 点「执行 SMT 分析」，查看状态、能力、单位、裕量、来源与建议。
6. 顶部「SMT 报告」输出 Excel 和 JSON，包含配置路径及版本。

格式：`schema_version: 1`；`meta.factory/revision` 必填；
`profiles.<名称>.rules` 定义 `id/field/label/unit/op`。
`op` 支持 `>=`、`<=`、`range`（对应 `value` 或 `min/max`）。
单位支持 mm/um/mil/count/ratio/percent/deg；同字段不得使用冲突量纲。
`scope` 支持 design（默认）和 process；`severity` 支持 high/medium/low。
模板中的 null 均代表未提供能力，不能产生 PASS。
SMT 页签目前提供定量能力匹配框架；封装自动识别、桥连和立碑几何识别待后续接入。
能力匹配与产线实际管控水平不同，后者需过程测量及验证数据。

---

> 一个把 **Cadence Allegro `.brd`** 变成可执行检查结果的桌面工具：
> 读板 → 自动填写 Layout Checklist → 输出 DFM 制程能力报告。
>
> 适用对象：Layout 工程师、DFM 工程师、需要给板厂提能力评估的人。
> 版本：2026-11（对应 `Evan_BRD.exe` 构建）

---

## 目录

1. [这个工具能做什么](#1-这个工具能做什么)
2. [快速开始](#2-快速开始)
3. [图形界面](#3-图形界面)
4. [BRD 数据提取](#4-brd-数据提取)
5. [Checklist 自动填写](#5-checklist-自动填写)
6. [DFM 制程能力分析](#6-dfm-制程能力分析)
7. [报告说明](#7-报告说明)
8. [命令行参考](#8-命令行参考)
9. [项目结构](#9-项目结构)
10. [打包发布](#10-打包发布)
11. [常见问题](#11-常见问题)
12. [BRD 分析与输出（8 大类能力）](#12-brd-分析与输出8-大类能力)

---

## 1. 这个工具能做什么

程序分两个，各干一件事，**互不依赖、各自导入自己的 BRD**：

| 程序 | 界面 | 干什么 |
|---|---|---|
| `Evan_BRD.exe` | **界面一** | 只做 **Checklist 自动填写**：读 `.brd` → 按规则填写 Layout Checklist → 输出填写报告 |
| `Evan_DFM.exe` | **界面二** | 只做 **DFM 分析**：自己导入 `.brd` → 制程能力比对 + 坐标定位 + 网络定位 + 器件统计 + 导出 DFM 报告 |

共用能力：

| 能力 | 说明 |
|---|---|
| **Checklist 模板自适应** | 换一版 checklist.xlsx 不用改代码：按**表头名字**识别列、按**条目内容**匹配规则 |
| DFM 制程能力比对 | 设计参数 vs 板厂能力库（江西红版 / 四会），逐项 PASS / 限值 / FAIL |
| 器件分类统计 | 按位号前缀（R/C/L/D/Q/U/IC/LED/J/SW/F/X/Y/T/B/BT）统计数量、封装、面别、坐标、引脚数，可导出 Excel |
| 报告输出 | Checklists 填写报告（界面一）+ DFM 报告（界面二），均为 Excel（多页签）+ TXT |

**能力边界**（这点比功能列表重要）：

- 不需要 Allegro 授权，**纯二进制解析** `.brd`，也不必用设计软件打开板子；
- `.brd` 里有**板框几何**（内嵌设计快照的 `BOARD GEOMETRY/OUTLINE`），板尺寸已能自动读出；
- 但 `.brd` 里**没有位号坐标、没有钻孔坐标**，这类数据必须由 PCB Editor 导出（SKILL / Placement / NC Drill），工具负责解析并与不符合项对接 → 见 [6.8 坐标定位](#68-坐标定位元件坐标--孔位坐标)；
- 不做 Gerber / ODB++ 解析，需要几何尺寸的项目请配合 CAM 工具；
- 结论口径是「**缺数据 ≠ 通过**」：能自动判的写依据，判不了的写清楚"该看什么"，不会伪造成 PASS；坐标同理——没有坐标来源就只给数量，不编位置。

---

## 2. 快速开始

### 2.1 启动方式

| 要做的事 | 打包版 | 源码 |
|---|---|---|
| 填 Checklist（界面一） | 双击 `dist\Evan_BRD.exe` | `python gui.py` 或 `python main.py --gui` |
| DFM 分析（界面二） | 双击 `dist\Evan_DFM.exe` | `python evan_dfm.py` |
| DFM 分析指定板子 | 把 `.brd` 拖到 `Evan_DFM.exe` 上 | `python evan_dfm.py xxx.brd` |
| 批处理 / CI | `Evan_BRD.exe --fill-checklist ... --brd ...` | `python main.py ...` |

> 两个都是单文件（onefile），首次启动要解压约 3–8 秒，属正常现象。
> 界面二**不需要**先开界面一：它自己导入 BRD，也自己记住上次用的板子。

### 2.2 五分钟上手（GUI）

**填 Checklist（界面一）**

1. 双击 `Evan_BRD.exe` → **① 输入文件** 选择 `.brd`；
2. 选择 **Layout Checklist**（`.xlsx` 模板）；
3. 点 **检查模板**（可先确认表头/列能识别，换过模板后尤其要确认）；
4. 输出目录默认 `<程序目录>/output`，可改；
5. **② 判定选项** 选板厂（hongban / sihui）、板型（rigid / flex）、等级、铜厚、填写人；
6. 点 **填写 Checklist 报告**（底部蓝色主按钮），右侧日志实时输出。

界面一底部只有三个按钮：**填写 Checklist 报告 / 演示运行 / 打开输出目录**（DFM 已经搬去界面二）。

**做 DFM 分析（界面二）**

7. 双击 `Evan_DFM.exe`（或把 `.brd` 拖到它上面）；
8. 左栏 **浏览** 选 BRD → 自动读入层数/线宽/线距/孔径/孔环/板厚/板尺寸（这些行默认隐藏，判定照用）；
9. 需要人工补的项（工艺边、拼板、器件到板边、测试点…）在左栏填，填完即算人工确认值；
10. 点 **执行分析** → 右栏「制程能力比对」页签；
11. 点 **导出报告** 存 DFM 报告；点 **坐标定位** 把不符合项落成"几个孔、在哪"。

### 2.3 一次运行会产出什么

| 文件 | 内容 |
|---|---|
| `<checklist名>_filled.xlsx` | 填好的 Checklist（原格式保留，只填空白格，不覆盖你已有的填写） |
| `<checklist名>_checklist_report.xlsx` | 填写报告：概览 / 逐项判定 / 需人工确认 / 不符合 / 待验证 / 模板识别 / DFM 校验摘要 |
| `<checklist名>_checklist_report.txt` | 同上摘要，纯文本，便于贴到邮件或评审记录 |
| `*_DFM报告.xlsx` / `.txt` | DFM 分析报告（界面二的「导出报告」） |
| `定位清单.xlsx/.csv/.txt/_locate.scr` | 坐标定位清单（界面二的「坐标定位」，见 6.8） |

> 示例：模板 `Layout_Checklist_Rev1.0.xlsx` 跑完会得到 `Layout_Checklist_Rev1.0_filled.xlsx` 和三份报告。

---

## 3. 图形界面

### 3.1 主窗口

![主窗口](docs/screenshots/ui_main.png)

界面一**只做 Checklist 填写**（窗口 1200×800，最小 1060×720，可自由缩放）：

```
┌────────────────────────────────────────────── 深色页眉 ──┐
│ Evan_BRD   Layout Checklist 自动填写                     │
├───────────────┬──────────────────────────────────────────┤
│ ① 输入文件     │                                          │
│  BRD / 清单    │            运行日志（深色）               │
│ ② 判定选项     │    info / success / warn / error 四色     │
│  板厂/板型/等级 │                                          │
├───────────────┴──────────────────────────────────────────┤
│ 填写 Checklist 报告 · 演示运行 · 打开输出目录             │
├──────────────────────────────────────────────────────────┤
│ 状态栏：Checklist 完成: PASS 3 / VERIFY 3 / 需人工 124 / N/A 5 │
└──────────────────────────────────────────────────────────┘
```

要点：

- **底部操作条常驻**，窗口再矮也不会把主按钮挤没；
- ① 卡片里多了一个 **检查模板** 小按钮：只读模板结构，告诉你它认到了哪张表、表头在第几行、每列当什么用——**换过 checklist 之后先点这个**；
- 日志区带「清空 / 复制全部」，排除故障时直接复制给开发；
- 运行期间三个按钮统一置灰，进度条出现在状态栏右侧；
- 关闭窗口会记住上次的路径与选项（见 3.4）。

### 3.2 DFM 分析窗口（界面二）

![DFM 分析窗口](docs/screenshots/ui_dfm_analysis.png)

**它是独立程序** `Evan_DFM.exe`，不是从界面一打开的二级窗口；BRD 由它自己导入。

- **左栏 设计参数**：BRD 文件（浏览 / 从 BRD 读取）、板厂 / 板型 / 能力等级 / 铜厚 / 表面处理 / 单板名、极性 / 拼板，以及需要人工补的 **设计参数**（按类别分组，滚轮滚动）；

其中 **极性已标注 / 已拼板** 是两个"人工确认开关"，`.brd` 里读不到，默认都勾上：

| 开关 | 勾上（默认） | 不勾 |
|---|---|---|
| 极性已标注 | 极性元件已有方向标识，不报问题 | 问题清单写 `ASM-01 组装/极性标识 高`：极性元件缺失方向标识，存在反向贴装风险 |
| 已拼板 | 该文件是拼板图，不报问题 | 问题清单写 `PNL-01 拼板/拼板方式 高`：未拼板，需拼板；若周边元件距板边 <5mm 会补一句"传送轨道撞车"描述 |
- **右栏 分析结果**：两个选项卡
  - **制程能力比对**：PASS / WARN / FAIL / INFO 彩色表格，多一列「来源」；
    - 表格上方有 **「坐标定位」** 按钮：把不符合项落成「几个孔、在哪」（见 6.8）；
- **页眉「器件统计」**：按位号前缀分类，统计器件数量 / 封装 / 坐标 / 引脚（见 6.11）；
- **页眉「网络定位」**：按网络名查坐标 / 位置 / 外框长宽（见 6.10）；
- **能力库全覆盖**：能力库（`config/dfm_capability_*.yaml`）里每个选项都会出一行，不再是"只显示能判的那几行"：
  - **已判定**：设计值存在，按方向（≥ / ≤）判 PASS / 接近 / FAIL；
  - **缺数据**：有对应设计字段但这次没值，写明"需要什么数据"；
  - **未接入**：能力库有这一项、程序没有对应设计字段，写明缺什么、建议人工核对；
  - 末尾一行 **「能力项覆盖清单」** 汇总"共 N 项：已判定 X / 缺数据 Y / 未接入 Z"，TXT 报告与 Excel「Main」页同款一行。
- **页眉按钮**：执行分析 / 器件统计 / 网络定位 / 导出报告 / 关闭；
- **状态栏**：板厂 · 板型 · 等级 | 结论 | 覆盖率 | BRD 数据几项 / 人工值几项。

#### 自己导入 BRD（不从界面一取）

界面二的 BRD 是**它自己的**：`浏览` 选文件、把 `.brd` 拖到 `Evan_DFM.exe` 上、
或直接命令行带路径都行；它把上次用的板子记在 `%APPDATA%\Evan_BRD\ui_state.json`
的 `dfm_brd` 里（和界面一的 `brd` 互不覆盖）。

选好 BRD 后自动读入，**读到的行默认隐藏**，不占界面：

| 状态 | 含义 |
|---|---|
| **BRD**（已隐藏） | 从 `.brd` 真实读到的值，判定时照用，只是不铺在界面上 |
| **手工**（蓝） | 你填的 / 改过的值（改一次就算人工确认），参与判定 |
| **待填**（灰） | BRD 没有、你也没填，判定时按"缺数据"处理，表格底部会列出来 |

要核对或覆盖 BRD 值（比如板厚是低置信的），勾选参数区右上角
**「显示 BRD 带入项 (12)」** 即展开；改完自动变成"手工"值。
点一次「执行分析」也会自动把这个按钮点亮并展开 BRD 带入项，方便对着结果看"这次判定到底引用了哪些 BRD 值"；
如果自己手动关掉过这个开关，程序尊重你的选择，不再自动打开。

表单**不预填任何数值**：空着就代表"这个数据我没有"，绝不用默认值凑数。
（万一某天字段带了预填值，会显示橙色「示例」标签并不参与判定，
此时右上角会出现「确认全部示例值」按钮，点一下即视为人工确认。）

实测某块 12 层板：48 项参数里 **16 项**由 BRD 直接给出并隐藏，界面只剩 **32 项**要人看，
其中大部分是"填了才算"的可选项（工艺边、拼板、器件到板边、测试点…）。
16 项是：铜层数、板尺寸 X/Y、最小线宽、最小线距、最小颈宽、内层线宽、内层线距、
不同网络孔壁间距、最小机械钻孔、最大机械钻孔、最小激光孔、单边孔环、板厚、
内层/外层线路到板边；表面处理（ENEPIG）也一并带进"看板选项"。

其中 4 项是**推算**（来源列会写明，不会当成实测）：

| 字段 | 怎么来的 | 注意 |
|---|---|---|
| 内层线宽 / 内层线距 | BRD 的 PhysicalCSet / SpacingCSet 只有一份**板级**约束（本板：`DEFAULT` + 网络类 `100`），不分内外层 → 按板级最小值代入 | 若设计自己建了 `INNER` 约束集，程序优先用 INNER 集 |
| 内层 / 外层线路到板边 | 内嵌快照里的 `ROUTE KEEPIN` 到板框的最小距离（本板 3.36mm，全层同一圈） | 这是**设计允许布线边界**，实际铜箔只会更靠内；因此判「满足」是可靠的，判「不满足」需人工复核 |

BRD 读完**不会在界面上铺来源清单**（不显示"已读到 16 项…/其余 32 项 BRD 不含…/钻孔跨度 N 种"，
也不显示板厚置信度提示）：读到的项＝参数表里的隐藏行，读不到＝界面上留着的待填行，
一眼就能看出还缺什么。只有"读失败""旧格式 BRD"这类真问题才留一行提示。

#### BRD 里没有的，为什么不能自动填

`.brd` 是**设计数据库**，存的是规则约束（线宽/线距/孔径）、网络、层、钻孔表，
外加一份设计快照（板框、丝印、装配图形）；位号坐标和孔位坐标不在里面。
阻焊、丝印、拼板、覆盖膜这些要 Gerber/图纸，程序不会去编。
典型的软板三项——覆盖膜开窗间距 / 开窗到焊盘 / 开窗到线路——BRD 里没有，
所以标「待填」；你把 CAM 实测值填进去（填完即标"手工"），程序照同一套制程能力表判
"是否超过"。例如量到开窗到线路 0.12mm，软板能力要求 ≥0.15mm，会直接判 FAIL：

```
FAIL  手工  覆盖膜   开窗边缘到线路距离: 设计 0.12mm 低于制程能力下限 ≥0.15mm
```

#### 判定口径

- 只有 **BRD 值** 与 **人工确认值** 去和 `config/dfm_capability_*.yaml` 比；
- 示例值、空值都不参与，表格底部会单独列一行"未确认/缺数据未参与判定"；
- 想快速把表单里的预填值当确认值，点一次「确认全部示例值」即可（等于一次性人工确认）；
- 覆盖率只统计真正确认过的输入，所以缺数据时结论会是"数据不足，不能作为放行依据"，
  不会给出假 PASS。

### 3.4 界面偏好记忆

关闭主窗口时，以下内容写入 `%APPDATA%\Evan_BRD\ui_state.json`，下次启动自动恢复：

BRD 路径、Checklist 路径、输出目录、Net List、板厂、板型、能力等级、铜厚、填写人。

文件缺失或损坏时按默认值启动，不影响使用；想恢复默认，删掉该文件即可。

> 顺带一提：窗口版没有控制台，所以程序把**界面日志**实时写到 `<exe 目录>\Evan_BRD_ui.log`
> （每次启动重写），未捕获的异常另写 `dist\Evan_BRD_crash.log`。万一点了没反应，
> 先看 `Evan_BRD_ui.log` 最后一行：它通常已经说清是缺路径、模板没认出来还是 BRD 读不出来。

---

## 4. BRD 数据提取

### 4.1 `.brd` 里到底有什么

Cadence Allegro 17.4 的 `.brd` = **二进制设计库** + 内嵌的几个 zip 归档。能解析的只有两类：

| 位置 | 内容 |
|---|---|
| 内嵌 zip | `objects/PhysicalCSet.xml`、`SpacingCSet.xml`、`SameNetSpacingCSet.xml`、`Design.xml`、`GenericLayer.xml`、`Header.xml` |
| 二进制体 | `_QuickViewText`（板名/单位/精度/层数/图形范围）、`DRILL CHART` 钻孔表、焊盘栈与过孔名（如 `VIA20D10A28`）、层名 `L<n>_<TYPE>`、制程说明文字 |

> 对象有没有, 看板子怎么存: 真实 PCB 设计一般带 `objects/Part.xml`(器件位号)、`Net.xml`、`DiffPair.xml`;
> FAB 拼板图/纯板框图纸只有约束对象和板框, **没有** Part.xml, 这时器件数量与位号如实报"读不到",
> 不拿丝印图形去猜。

### 4.2 能提取到的字段

| 字段 | 来源 | 置信度 | 备注 |
|---|---|---|---|
| 铜层数 | `_QuickViewText` 的 `Layers:` | 高 | 权威值；缺失时回退层名统计 |
| 单板名 | `Design.xml` / `_QuickViewText Name:` | 高 | 用于报告标题 |
| 最小线宽 | `PhysicalCSet.xml` | 高 | 自动单位换算 |
| 最小线距 | `SpacingCSet.xml` 的 `LINE_TO_LINE_SPACING` | 高 | 自动单位换算 |
| 最小颈宽 | `PhysicalCSet.xml` 的 `MIN_NECK_WIDTH` | 高 | 按最小线宽口径提示，不单独判 FAIL |
| 不同网络孔壁间距 | `SpacingCSet.xml` 的 `THRUVIA_TO_THRUVIA_SPACING` | 高 | 板级最小值 |
| 最小机械钻孔 | 钻孔表 + 焊盘栈命名 | 高 | 两者取更小值，并附校验提示 |
| 最大机械钻孔 | 钻孔表 PTH 最大孔径 | 高 | 安装孔也算机械孔 |
| 激光孔 / 盲埋孔 | 钻孔跨度 | 中 | 无盲埋孔则不带入 |
| 最小激光孔 | 钻孔表盲/埋孔最小孔径；无钻孔表时退回焊盘栈里真激光孔（<0.15mm） | 中 | 两个来源都写在来源说明里，不一致时提示取更小值 |
| 引脚间距（焊盘中心距） | SKILL 导出的焊盘外框（`evan_pads.csv`）优先；没有导出时退回内嵌快照焊盘图形 | SKILL 高 / 快照 中 | 见 6.9：快照只有焊盘**外框**（钢网层矩形/圆），没有位号与网络，所以按几何聚类成"簇#n"，报告里明确写"不是真实位号" |
| 焊盘间距（边缘距） | 同上（圆/矩形/旋转矩形都支持） | 同上 | 中心距 + 边缘最短距都在 `src/pad_geometry.py`；一个器件里按"最常见中心距"给出主间距（排除两端跨度） |
| 焊盘到铜皮 / 到走线距离 | 焊盘几何 + 内嵌快照 ETCH 图形（走线中心线±线宽、闭合铜皮轮廓算内部） | 中 | 见 6.9：只算**未相连**的铜箔，相接/相交的单列；焊盘落在铜皮轮廓内只标"在铜皮内"不给数字 |
| 丝印线宽 | 快照里 class/subclass 含 `SILKSCREEN` 的图形 `width` | 中 | 有非 0 宽度就取最小值判定；本库 78 块板 width 全是 0，界面如实提示"快照未记录线宽" |
| 丝印离成型线 | 丝印线段到 `BOARD GEOMETRY/OUTLINE` 的几何最短距离 | 中 | ≤0.05mm 不当判定值（可能是板框丝印边框压线），只在界面提示人工确认 |
| 丝印到器件 | 板级丝印到 `PACKAGE GEOMETRY/PLACE_BOUND·ASSEMBLY` 本体轮廓的几何最短距离 | 中 | 快照没有焊盘，器体用占位框/装配轮廓代替；组件自身丝印不计（否则恒为 0） |
| 测试点 / 焊盘 | 钢网层 `PASTEMASK` 圆焊盘按测试点识别，取最小直径 | 中 | 有焊盘图形才能出数（见 6.9）；本库 78 块板快照里没有 PAD/PASTEMASK/SOLDERMASK 图形，界面如实报缺数据 |
| 单边孔环（机械孔） | 焊盘栈命名 | 中 | 只算**通孔**焊盘栈，孔型判定见 4.3 |
| 单边孔环（激光/盲埋孔） | 焊盘栈命名 | 中 | 盲孔、埋孔、孔径 <0.15mm 的激光孔 |
| 板厚 | 二进制制程说明文本 | **低** | 取自 `PCB`/`OVERALL` 关键字行，必须人工核对 |
| 表面处理 | 制程说明文本 | **低** | 如 ENEPIG/ENIG，带进"看板选项"下拉 |
| 板尺寸 X / Y | 内嵌设计快照 `BOARD GEOMETRY/OUTLINE` | 高 | 用 `.brd` 里的 Draw Size（mil）标定，双方向系数必须一致（差 >2% 就降级） |
| 器件数量 / 位号 | `objects/Part.xml` 位号节点 | 高（有该对象时） | 去重后按"前缀+数字"排序，按 R/C/U/J… 归类统计 |
| 器件分类明细 | `objects/Part.xml` + 位号前缀表 | 高 | 如 电容 557 / 测试点 451 / 电阻 394 / IC 48 |
| PTH / NPTH 孔分类 | 钻孔表 `PTH`/`NPTH` 列 + 跨度 | 高 | 给出每种镀覆方式的孔种数与孔径范围 |
| NPTH 孔到成型线 | 需要**孔坐标** | — | `.brd` 快照只有孔径/跨度/数量，没有坐标，算不了；界面如实写"缺孔坐标" |

每个字段都带「来源 + 置信度」，报告和日志里都会写出来，不玩"看起来像真的"。

#### 孔型怎么定（机械孔 / 激光·盲埋孔 / 待确认）

孔环能力表里机械孔和激光孔是两个数（本厂 6mil / 4mil），**先把孔分对，再谈比得上比
不上**。只看孔径大小是不够的：8mil 的孔既可能是通孔，也可能是 L3~L10 的埋孔，把埋孔
的 3mil 孔环当成机械孔去比 6mil，结论就是错的。所以按三条证据判：

| 证据 | 判成 |
|---|---|
| 焊盘栈名字里带跨层标记，如 `VIA14D8_3-10` | 盲/埋孔 |
| 孔径 < 0.15mm | 激光孔 |
| 钻孔表里该孔径只出现在通孔跨度（TOP to BOTTOM） | 机械通孔 |
| 钻孔表里该孔径只出现在盲/埋跨度（L3 to L10） | 盲/埋孔 |
| 同孔径两种跨度都有，且有多个焊盘栈 | 启发性判据：焊盘最大的按通孔，其余按盲/埋孔（通孔要留更大孔环对 Z 向对位） |
| 同孔径两种跨度都有，但只能是一个焊盘栈 | 这个栈通孔、盲孔都在用 → 两类孔环能力都要满足，两行都算上 |
| 钻孔表里查不到该孔径（很多板子没有钻孔表） | 无证据可依，按口径归类：≥0.15mm 归机械通孔、<0.15mm 归激光孔，来源写明「按孔径口径」 |
| 孔径 ≥0.6mm | 安装孔/定位孔，一律按通孔，不采信钻孔表的跨度列（有的板子整表都是模板默认跨度） |

实测 860-000678：8mil 孔径既有通孔（158 个）又有埋孔（272 个），程序把 8/16 的
`VIA8-16` 判为通孔（孔环 0.1016）、把 8/14 的 `VIA14D8_3-10`（名字带 `_3-10`）等
判为盲埋孔（孔环 0.0762）。于是判定表上是：

| 行 | 设计值 | 板厂能力（红版常规） | 结论 |
|---|---|---|---|
| 单边孔环(机械孔) | 0.1016mm | ≥0.1524mm（6mil） | 不通过 |
| 单边孔环(激光/盲埋孔) | 0.0762mm | ≥0.1016mm（4mil） | 不通过 |

也就是说，0.0762 这个 3mil 孔环现在只出现在**激光/盲埋孔**那一行，不会再被当成机械孔；
机械孔一行只拿通孔焊盘栈（本板 `VIA8-16`）去比。若某个焊盘栈的孔型确实判不出来
（同孔径两种跨度、又分不开），会单列 `单边孔环(孔型待确认)` 的 info 行，写明是哪几个
焊盘栈、为什么判不出、若是通孔该和哪个能力值比，不硬塞进任何一边。

### 4.3 单位判定

`.brd` 里的数值单位不统一：有的板子存 mil，有的存 mm，个别约束表默认值本身也有问题。

程序不靠猜命名，而是**按量级打分**：把候选单位（mil / mm）都换算一遍，用"是否符合制程常识"给每个值打分，取总分最高的一组。这样能避免把 `0.0023 mm` 这种明显异常值当成真实值送进判定。

### 4.4 拿不到的数据

以下项目 `.brd` 二进制里没有，界面与报告都会明确列出，**不会**当作通过：

阻焊桥、阻焊开窗、丝印线宽/间隙、器件间距、基准点数量、器件到板边、NPTH 到板边、测试点直径、工艺边宽度、拼板尺寸、Mark 点、板材利用率、覆盖膜参数。

> 板框尺寸本来也在这张表里，现在已能从内嵌设计快照算出来（见 4.2）。其余项目要靠 Gerber / 坐标或人工测量。

另外两类数据 `.brd` 里根本没有，但可以"问"设计软件要：

| 数据 | 为什么 .brd 里没有 | 怎么拿 |
|---|---|---|
| 器件位号 + X/Y 坐标 | 位号、placement 存在 Allegro 数据库，不在嵌入的设计快照里；快照只有丝印/装配图形 | PCB Editor 跑本工具生成的 SKILL 脚本，或 `File > Export > Placement` |
| 每个孔的坐标 | 二进制里只有钻孔表（孔径 + 数量），没有孔位 | SKILL 导出，或 `Manufacture > NC > NC Drill` 导出 Excellon |

拿到之后用「坐标定位」把它们和 DFM 不符合项对起来，见 [6.8](#68-坐标定位元件坐标--孔位坐标)。

### 4.5 数据校验（sanity gate）

每个字段都有合理区间（如线宽 0.01–5mm、钻孔 0.05–6.6mm、板厚 0.3–10mm）：

- 超区间 → 丢弃该值并记一条 `[校验]` 警告；
- 钻孔表不完整（例如最小值明显偏大）→ 与焊盘栈交叉比对取小值 + 警告；
- 板厚只取自说明文字 → 低置信标记，报告注明「需核对」。

### 4.6 实测覆盖率（同一套代码，4 块真实板）

| 板号 | 层数 | 最小线宽 | 最小线距 | 最小钻孔 | 单边孔环 | 板厚 | 覆盖率 |
|---|---|---|---|---|---|---|---|
| 860-000776 | 4 | 0.0991 | — | 0.254 | 0.127 | — | 3/15 = 20% |
| S-860-000707 | 10 | 0.09 | 0.09 | 0.2032 | 0.1016 | 1.6 | 5/15 = 33% |
| 860-000678 | 12 | 0.1001 | 0.127 | 0.2032 | 0.1016 | 1.6 | 5/15 = 33% |
| S-860-000000 | 6 | 0.0899 | 0.1016 | 0.254 | 0.1016 | — | 4/15 = 27% |

结论：**光靠 `.brd` 只能覆盖 20%–33% 的关键输入**。这就是为什么 DFM 报告一定要显示覆盖率——只报「PASS」会害人。

---

## 5. Checklist 自动填写

### 5.1 六个步骤

| 步骤 | 动作 | 产物 |
|---|---|---|
| 1/6 | 读取 `.brd`（或 Net List JSON） | 层数、网络、约束库、孔径统计、板厚 |
| 2/6 | 信号自动分类 | 16 类网络 + 差分对 |
| 3/6 | 按类别生成约束 | 物理 / 间距 / 电气 / 过孔约束（共 39 条） |
| 4/6 | DFM 制程能力校验 | PASS / 限值 / FAIL + 覆盖率 |
| 5/6 | 识别 Checklist 模板 | 工作表、表头行、列映射、条目数 |
| 6/6 | 逐项判定并填写 | `*_filled.xlsx` + 填写报告 |

每一步都在右侧日志里回报，例如：

```
[1/6] 读取 BRD...            -> 12 层 / 854 网络
[4/6] DFM 校验 (hongban rigid-standard)...
      -> 结论 不通过, 错误 3 / 警告 3, 覆盖 33%
[5/6] 识别 Checklist 模板...
      -> 工作表 check list / 表头第 3 行 / 识别方式 表头识别
      -> 检查项 135 项
[6/6] 逐项判定并填写...
      -> PASS 3 / FAIL 0 / 需人工 124 / 不适用 5
      -> 规则命中 36 条, 未覆盖 10 项
```

### 5.2 五种判定状态

| 状态 | 写进「检查」列的内容 | 含义 |
|---|---|---|
| PASS | `PASS` | 有 BRD 数据支撑，且满足 |
| VERIFY | `VERIFY` | 规则命中但需要 DRC / 仿真落到实板（例如已定义 3W 规则 ≠ 已按 3W 布线） |
| FAIL | `FAIL` | 有数据证明不满足（样本板没有出现，说明规则库没乱判） |
| MANUAL | `MANUAL` | BRD 里没有这类数据（器件坐标、丝印、拼板图），需要人工看 |
| N/A | `N/A` | 本板不涉及（例如没有差分对时的「差分等长」） |

三种自动结论都会在「原因说明」列写清依据，前缀便于筛选：

```
[自动] BRD 内已定义 4 类间距规则 (最小线线间距 0.127 mm), 需 DRC 验证
[待验证] BRD 内已定义 3W 间距规则 (0.381 mm), 需 DRC 验证实际布线
[不适用] BRD 内识别到 0 对差分信号, 本项不适用
[人工] 布局类: 需目视/3D 确认器件位置、干涉与散热 (BRD 不含几何)
```

「签核」列写 `填写人 / 时间`。**只填空白单元格**，你原来已经写过的内容不会被覆盖。

### 5.3 规则库怎么工作

全部规则在 `config/checklist_auto_rules.yaml`，共 41 条，分两段：

```yaml
template:            # 模板长什么样 (见 5.4)
  sheet_candidates: ["check list", "checklist"]
  header_scan_rows: 15
  columns: {...}
rules:               # 每条检查项怎么判 (见 5.5)
  - id: aspect_ratio
    all: ["过孔孔径", "板厚"]
    desc: 过孔纵横比
    judge: {kind: compare, field: min_mech_drill, op: ">=", other: board_thickness, scale: 0.1}
    pass: "最小机械孔径 {min_mech_drill} mm ≥ 板厚/10 ({board_thickness} mm ÷ 10)"
```

匹配靠**条目文字**，不靠编号——所以模板改版、条目挪位置都还能命中：

- `all`: 全部关键词都出现（AND）
- `any`: 出现任意一个（OR）
- `not`: 出现任一就排除
- `category`: 只在该类别里找
- `when`: 满足条件才判，否则填 N/A

命中后用 `judge` 判定，五种判据：

| kind | 作用 | 例子 |
|---|---|---|
| `compare` | 字段与数值/另一字段比较 | `min_mech_drill >= board_thickness * 0.1` |
| `dfm` | 取 DFM 校验里某一项的结论 | 「最小线宽」= pass |
| `dfm_all` | 所有 DFM 项都通过才算通过 | 整板 DFM |
| `count` | 字段计数达到下限 | `spacing_rule_count >= 1` |
| `attest` | 工具已完成的动作，附说明 | 已生成 39 条约束 |

`result: verify` 的规则不写 PASS 而写 VERIFY；`when` 不满足则写 N/A。

一条都没命中的条目 → `MANUAL`，原因按**类别**给（布局类 / 间距类 / 丝印类 / 测试类…），
并在报告「模板识别」页列出未覆盖清单。

### 5.4 换了一版 checklist.xlsx 怎么办

这是本工具的重点设计：**模板改了不用改代码**。程序按下面顺序认模板：

1. 工作表：先找 `check list` / `checklist`，找不到再找名字里含 check 的，最后用最后一个 sheet（并给警告）；
2. 表头行：在前 15 行里逐行打分——某一行能匹配到最多已知列名的就是表头行（编造不出来的话退回旧模板固定列并给警告）；
3. 列位置：按列名别名映射 `类别 / 子类 / 编号 / 级别 / 条目内容 / 符合标准 / 原因说明 / 检查 / 签核`，**列顺序随便变、中间插列都不影响**；
4. 条目：从表头下一行开始读，编号用正则取数字（`3.`、`No.3`、`３` 都认），类别/子类向下填充（合并单元格也能读）；
5. 只要求三列必须能认出来：**编号、条目内容、检查**（缺了会在「检查模板」里明确告诉你）。

改完模板后的标准动作：

```
1) 选好新的 xlsx
2) 点「检查模板」→ 看提示框
3) 表头行/列位置对得上 → 直接跑
   对不上 → 看下面三种情况
```

| 你改了什么 | 程序的表现 |
|---|---|
| 调整列顺序 | 自动适应，无需处理 |
| 中间插入一列（如「子类别」） | 自动适应，新列被忽略或识别为子类 |
| 表头改叫法（「条目内容」→「检查内容」、「检查」→「检查结果」） | 内置别名表已覆盖常见说法；没覆盖的会提示「列识别失败」 |
| 新增/删除检查项 | 自动按新条目数处理，规则按文字匹配 |
| 换工作表名（`check list` → `Layout CheckList`） | 自动找含 check 的 sheet |
| 表头下移一行（多了一行标题） | 前 15 行内自动找 |

> 提示框里出现「未找到表头 / 列识别失败」时，把新的列名加进
> `config/checklist_auto_rules.yaml` 的 `template.columns` 对应别名列表即可，例如：
>
> ```yaml
> columns:
>   items: ["条目内容", "检查内容", "检查项", "Items"]
>   check: ["检查", "检查结果", "结果", "Check"]
> ```
>
> 打包版：把 `config` 文件夹放到 `Evan_BRD.exe` 旁边，程序**优先读外部这份**，
> 不用重新打包就能改规则、加别名。

### 5.5 自己加一条判定规则

场景：公司要求「过孔到板边 ≥ 0.5 mm」，模板里有一条这样的检查项，但目前是 MANUAL。

**第一步**，在「检查模板」或报告的「模板识别」页找到该条目的原文，比如：
`过孔边缘到板边距离是否大于0.5mm`。

**第二步**，在 `config/checklist_auto_rules.yaml` 的 `rules` 里加：

```yaml
  - id: via_to_edge
    all: ["过孔", "板边"]
    desc: 过孔到板边距离
    judge: {kind: compare, field: copper_to_edge_outer, op: ">=", value: 0.5}
    pass: "外层铜到板边 {copper_to_edge_outer} mm ≥ 0.5 mm"
    fail: "外层铜到板边 {copper_to_edge_outer} mm < 0.5 mm, 需移动过孔"
    missing: "BRD 未提取到板边距离, 需人工测量"
```

**第三步**，重启程序（源码运行时直接重跑）。规则按**从上到下第一条命中**生效，
所以具体规则要写在通用规则前面。

可用的字段（`{字段}` 会替换成实际值）：`layer_count`、`board_thickness`、
`min_line_width`、`min_spacing`、`min_mech_drill`、`min_laser_via`、`min_annular_ring`、
`copper_um`、`board_size`、`net_classes`、`diff_pairs`、`physical_count`、`spacing_count`、
`electrical_count`、`via_count`、`constraint_total`、`spacing_rule_count`、`spacing_rule_min`、
`has_3w_rule`、`impedance_rule_count`、`part_count`、`hole_count`、`dfm_error_count`、
`dfm_warning_count`、`dfm_coverage`、`dfm_fail_count`。

### 5.6 真实样本跑出来是什么样

样本：`860-000678_FAB_BRD_Rev_A.brd`（12 层，854 网络）+ `Layout_Checklist_Rev1.0.xlsx`（135 项）

| 结果 | 数量 | 说明 |
|---|---|---|
| PASS | 3 | 约束已生成（39 条）、物理/电气规则已按 12 个网络类生成、过孔纵横比 0.2032 ≥ 1.6×0.1 |
| VERIFY | 3 | 3W 规则已定义（0.381 mm），需 DRC 验证实际布线 |
| N/A | 5 | 全板无差分对，差分阻抗/等长类不适用 |
| MANUAL | 124 | 布局、丝印、拼板、测试点等 BRD 里没有几何数据的项 |
| 未覆盖 | 10 | 规则库没有对应规则，报告里逐条列出 |

**为什么 MANUAL 这么多**：Cadence 的 `.brd` 二进制里存的是约束库和网络表，
**不含走线/坐标/丝印几何**。凡是「量距离、看位置」的条目，程序就只能告诉你该看什么，
不会伪造成 PASS。想要这些项也自动判，需要 Gerber / ODF++ / Allegro 二次开发取 Geometry，
这是工具当前明确的能力边界。

**自动判定率低不等于没用**：填写报告会把 124 条人工项按类别整理成待办清单，
每条都写清楚「查什么」，比对着 135 行空白表格逐条想快得多；规则库也可以按公司口径持续追加。

---

## 6. DFM 制程能力分析

### 6.1 两种入口

| 函数 | 用途 |
|---|---|
| `validate_constraints()` | 对**已生成的约束**做校验（主流水线用，旧接口兼容） |
| `analyze_design()` | 对**设计参数**做比对（界面二 / `--dfm`） |

参数从哪来有两条路，都走 `src/dfm_inputs.brd_design_values()` 这一份映射：

- **界面**：打开 DFM 窗口 → 自动读 BRD → 你补 BRD 没有的 → 执行分析；
- **命令行 `--dfm` / 界面二「导入 BRD + 执行分析」**：`extract_brd_design()` → `to_design_params()` →
  `analyze_design()`，只带 BRD 值（没有的项就是缺数据，不猜）。

### 6.2 能力库

`config/dfm_capability_hongban.yaml`（江西红版）、`config/dfm_capability_sihui.yaml`（四会），来自板厂能力表 Excel。

结构：`板厂 → 板型 → 等级 → 13 个能力段`

| 板型 | 等级 |
|---|---|
| 硬板 rigid | standard（常规）、automotive（车规）、extreme（极限） |
| 软板 flex | standard、extreme |

能力段：`panel`、`trace`、`lamination`、`drill`、`plating`、`plug_hole`、`solder_mask`、`silkscreen`、`surface_finish`、`test`，软板另有 `coverlay`、`routing`、`stiffener`。

> 同一能力项会按铜厚分档（如线宽线距按成品铜厚 0.5/1/2/3oz，阻焊桥按铜厚分档），程序按你填的铜厚自动取档。

### 6.3 判定逻辑

```
design_val ≥ capability  → PASS（higher_is_better，如线宽、孔环）
design_val ≤ capability  → PASS（如钻孔上限、板厚上限）
进入能力的 80% 危险区   → WARN（限值提醒，不算失败）
超出能力               → FAIL
能力库里没这一项        → INFO（需与板厂评估）
没有已确认的输入        → 不判定，计入"缺数据未参与判定"（README 口径: 没查 ≠ 通过）
```

`design_val` 只接受两种来源：`.brd` 读到的值、你手工确认过的值。表单预填的示例值
（橙色「示例」标签）不会传进比对引擎 —— 这是为了避免拿一个从未核实过的数字去和
板厂能力比，比完再报 PASS。

### 6.4 检查项一览

| 类别 | 检查项 |
|---|---|
| 拼板 | 板尺寸 vs 最大拼板 |
| 压合 | 板厚范围；能力库没给范围时退化为"板厚"信息行（带来源与置信度） |
| 线路 | 最小线宽、最小线距、不同网络孔壁间距、内层/外层线路到板边 |
| 钻孔 | 最小/最大机械钻孔、最小激光孔、单边孔环（机械孔 / 激光孔）、PTH 纵横比、NPTH 孔到板边（缺坐标时只提示不判定）、PTH/NPTH 孔分类 |
| 板信息 | 器件数量（`Part.xml` 位号清单，含分类统计与位号示例） |
| 阻焊 | 最小阻焊桥、阻焊开窗最小尺寸 |
| 塞孔 | 树脂塞孔条件（孔径 + 板厚） |
| 丝印 | 最小文字线宽、最小文字间隙、最小字符尺寸、丝印离成型线、丝印到器件（后两项几何算）；测试点按钢网层圆焊盘识别（缺焊盘数据时如实报） |
| 覆盖膜 | 开窗最小间距、到焊盘、到线路、最小开窗（仅软板） |
| 测试 | 电测 PAD 二线 / 四线 |

### 6.5 覆盖率与结论口径

覆盖率统计 **15 项关键输入**（软板 +3 项覆盖膜 = 18 项）：

最小线宽、最小线距、最小机械钻孔、单边孔环、板厚、阻焊桥、阻焊开窗、丝印线宽、丝印间隙、器件间距、基准点、器件到板边、测试点直径、工艺边、Mark 点。

结论按下表给出（注意第 2 条，这是本工具和"报喜型工具"最大的区别）：

| 条件 | 结论 `verdict` | 可否放行 `releasable` |
|---|---|---|
| 有 FAIL | 不通过 | 不可以 |
| 覆盖率 < 50% | 数据不足，不能作为放行依据 | 不可以 |
| 有 WARN | 通过（有风险项） | 覆盖率 ≥60% 时可以 |
| 其余 | 通过 | 覆盖率 ≥60% 时可以 |

### 6.6 评分与风险

- 严重度权重：危险 10、高 5、中 2、低 0.5；
- 可制造性评分 = `100 − 扣分`（最低 0）；
- 风险等级取最高严重度：危险 / 高 / 中 / 低；
- 预估良率 = 评分 ±2%（粗略区间，仅供参考）；
- 问题编号带类别前缀：`FAB-TR`（线路）、`FAB-DR`（钻孔）、`FAB-SM`（阻焊）、`FAB-SK`（丝印）、`FAB-PL`（塞孔）、`FAB-CV`（覆盖膜）、`FAB-TS`（测试）、`FAB-LM`（压合）、`PNL`（拼板）、`ASM`（组装）。

### 6.7 参数表（48 项，标了来源）

> 界面二里 BRD 能给的 16 项默认隐藏（判定照用），界面上只留 32 项；下表是完整数据模型。

按类别分组；每行右侧的标签就是取值来源（BRD / 手工 / 示例 / 待填），
**只有 BRD 与手工值参与判定**。

| 类别 | 字段 | 能否从 BRD 读 |
|---|---|---|
| 板信息 | 铜层数、器件数 | 能（层数来自元数据；器件数在 Part 表完整时给出） |
| 拼板/板尺寸 | 板尺寸 X / Y | 能（内嵌设计快照的板框，Draw Size 标定） |
| 线路 | 最小线宽、最小线距、最小颈宽、不同网络孔壁间距 | 能（PhysicalCSet / SpacingCSet） |
| 线路 | 内层线宽 / 内层线距 | 能，但**是板级值**：BRD 的约束只有一份板级最小值（本板 `DEFAULT` + 网络类 `100`），不分内外层 → 按板级最小值代入，来源注明"未分内外层"；若设计建了 `INNER` 约束集则优先用它 |
| 线路 | 内层 / 外层线路到板边 | 能**推算**：内嵌快照 `ROUTE KEEPIN` 到板框的最小距离（本板 3.36mm）＝设计允许布线边界；实际铜箔只会更靠内，报告里注明"非实测铜箔位置"。快照里没有 keepin 或 keepin 与板框重合时不填 |
| 钻孔 | 最小/最大机械钻孔、最小激光孔、单边孔环、激光孔单边孔环、板厚 | 能（钻孔表 + 焊盘栈命名 + 制程说明） |
| 钻孔 | NPTH 孔到板边 | 不能（要板框） |
| 阻焊 | 阻焊桥、阻焊开窗、阻焊油墨厚度 | 不能（图形层） |
| 塞孔 | 塞孔孔径、树脂/油墨塞孔深度 | 不能（工艺单） |
| 丝印 | 丝印线宽、间隙、到阻焊、离成型线 | 部分：线宽看快照 `width`（全 0 时提示人工测）、离成型线由丝印几何算；间隙/到阻焊需阻焊与其它图形 |
| 组装 | 器件最小间距、A/B 面基准点数、器件到板边、测试点直径、片式焊盘对称比、BGA 间距、BGA 单边孔环 | 大部分不能（要装配坐标）；**焊盘尺寸、每颗器件的引脚间距、焊盘到铜皮/走线距离已能出数**（见 6.9），前提是有焊盘几何（SKILL 导出 `evan_pads.csv` 或快照自带焊盘图形） |
| 拼板 | 拼板尺寸 X / Y、工艺边宽度、Mark 数量、Mark 到工艺边、定位孔数量、板材利用率 | 不能（拼板图） |
| 覆盖膜(软板) | 开窗间距、到焊盘、到线路 | 不能（覆盖膜属图形层，需 CAM/图纸） |
| 测试 | 电测 PAD 二线 | 不能（测试点坐标） |

铜厚只在「看板选项」里填 oz（1oz≈35um），避免 um/oz 两个输入互相打架。

### 6.8 坐标定位（元件坐标 / 孔位坐标）

DFM 说「最小机械钻孔 0.199mm，能力 0.2mm，超能力」之后，车间问的下一句一定是：**多少个？在板子哪里？** 这一节就是回答这个。

#### 前提：坐标不在 `.brd` 里

`.brd` 的二进制只有约束、网络、层和钻孔表（孔径 + 数量），**没有位号坐标、没有孔位坐标**。所以工具不去猜，而是提供三条"接入坐标"的路，读到多少说多少：

| 来源 | 能给出什么 | 怎么产生 | 精度 |
|---|---|---|---|
| **SKILL 导出**（推荐） | 元件位号 + X/Y + 旋转 + 面；过孔/通孔坐标 + 焊盘栈 + 网络 | 本工具生成脚本，在 PCB Editor 里跑一次 | 与设计数据库完全一致 |
| **Placement 导出** | 只有元件位号 + X/Y + 旋转 + 面 | `File > Export > Placement`（`place_txt.txt`） | 一致，但没有孔位 |
| **Excellon 钻孔文件** | 每个孔的 X/Y + 刀具直径 | `Manufacture > NC > NC Drill` | 与钻孔数据一致，但没有元件 |

#### 第一步：在 PCB Editor 里生成坐标

GUI：DFM 分析窗口 →「坐标定位」→「生成 SKILL 导出脚本」。
命令行：

```bash
# 生成 evan_pick.il / evan_pick.scr / README_坐标导出.txt
dist\Evan_BRD.exe --export-skill "D:\out\allegro_bridge"
```

然后在 PCB Editor（**不是**只读的 Free Viewer）命令窗口里执行两行：

```skill
skill load("D:/out/allegro_bridge/evan_pick.il")
evan_pick_all("D:/out/allegro_bridge")
```

产出：

| 文件 | 内容 |
|---|---|
| `evan_components.csv` | `位号, X(DBU), Y(DBU), 旋转, 面` |
| `evan_holes.csv` | `类型(VIA/PIN), 焊盘栈, X(DBU), Y(DBU), 网络, 位号` |
| `evan_pads.csv` | `类型(PIN), 焊盘栈, X(DBU), Y(DBU), 宽(DBU), 高(DBU), 面, 网络, 位号, 引脚号` —— 每个引脚的焊盘外框，"焊盘尺寸 / 引脚间距 / 到铜皮·走线距离"用这份最准 |
| `evan_pick.log` | 每一步的条数；SKILL 属性名不匹配时错误也写在这里 |

CSV 首行是 `UNITS,<单位>,<精度>,<每单位DBU>`，坐标按 DBU 整数原样输出，**换算成 mm 在 Python 侧做**，避免 SKILL 里做单位换算引入误差。孔位直径优先取钻孔文件刀具表，其次从焊盘栈名（`VIA20D10A30` = 焊盘 20mil / 钻孔 10mil）解析。

#### 第二步：生成定位清单

GUI：同一个窗口点「生成定位清单」；命令行：

```bash
dist\Evan_BRD.exe --pick "D:\...\860-000678_FAB_BRD_Rev_A.brd" ^
    --pick-files "D:\out\allegro_bridge\evan_components.csv" ^
                 "D:\out\allegro_bridge\evan_holes.csv" ^
    --output "D:\out\定位清单"
```

不给 `--pick-files` 时会自动在 BRD 同目录和输出目录里找 `evan_*.csv` / `place_txt.txt` / `*.drl`。

产物（默认写到 BRD 同目录的 `定位清单\`）：

| 文件 | 内容 |
|---|---|
| `定位清单.xlsx` | 6 个页签：汇总 / 不符合项定位 / 孔位尺寸汇总 / 全部孔位 / 元件坐标 / 数据来源 |
| `定位清单.csv` | 不符合项 + 命中数量 + 说明（给不进 Excel 的系统用） |
| `定位清单.txt` | 纯文本摘要，每条不符合项列出前 10 个坐标 |
| `定位清单_locate.scr` | Allegro 脚本：`zoom center x y` 逐个跳到不符合位置，`File > Script` 直接跑 |

![坐标定位窗口](docs/screenshots/ui_locate.png)

#### 匹配口径（重要，别误解）

| 检查项 | 怎么给坐标 |
|---|---|
| 最小机械钻孔 / 最小激光孔 | 先按**设计值**精确匹配（如 0.1992mm ± 2%），命中就是"这个尺寸的孔有几个、在哪"；没有同尺寸孔时退回"小于能力值"的孔并注明 |
| 最大机械钻孔 | 列出大于设计值的孔 |
| 单边孔环（机械孔 / 激光孔） | 逐孔算 `(焊盘-钻孔)/2`，列出小于能力值的孔及其实测孔环；焊盘栈名解析不出焊盘直径时如实写"不能判断孔环" |
| 孔壁间距、PTH 纵横比、线宽线距、阻焊、丝印等 | **不给坐标**，写明「需要图形几何 (CAM/Gerber)」——这些判定依赖铜箔/阻焊的形状，不是靠孔位 |

也就是说：**能确定的给坐标，不能确定的直说**。0 个命中也照样输出（`命中 0 个孔`），不把"没找到"包装成"没问题"。

#### 单位与坐标对不上怎么办

- SKILL 导出：CSV 首行自带单位与 `dbuperuu`，工具自动换算，不要手改；
- Placement 导出：`# Units: mils` 这类注释会被读取；没有注释时按 mils 处理并在「数据来源」页签标注；
- Excellon：`METRIC` / `M71` / `M72` / `FILE_FORMAT=2:5` 都支持；
- `locate.scr` 里的坐标按**设计用户单位**换算过（来源是 mil 就换回 mil），不会出现"跳错位置"。

### 6.9 焊盘尺寸 / 引脚间距 / 焊盘到铜皮与走线的距离

用户最常问的三件事：**每个器件的焊盘多大、引脚间距多少、焊盘离铜皮和走线还剩多少**。
BRD 里未必直接给，所以按"能拿到的最可信来源"分两步做，来源都写进报告：

| 步骤 | 数据来源 | 拿到什么 | 拿不到时 |
|---|---|---|---|
| ① 焊盘几何（优先） | SKILL 导出 `evan_pads.csv`（`--export-skill` 生成脚本，PCB Editor 里跑一次） | 每个引脚的焊盘外框 宽×高（mm）、位号、引脚号、面别 | 退回 ②，并在报告里写明"用的是快照几何，不是设计数据库" |
| ② 快照焊盘图形 | `.brd` 内嵌设计快照里的 `PAD/PIN`、`PASTEMASK`、`SOLDERMASK` 图形（矩形闭合轮廓拼成环求长宽，圆按直径） | 焊盘中心 + 尺寸，但**没有位号、没有网络** | 如实报"缺数据"，并给出 ① 的导出配方 |

焊盘层按 `钢网 (PASTEMASK) > PAD/PIN > 阻焊 (SOLDERMASK)` 取一个：钢网层最接近真实焊盘，
阻焊层含开窗外扩。同一中心位置的多层焊盘只算一次，报告注明"重叠 n 个，已按导出值计"。

**没有位号时怎么分器件**：按"焊盘边缘间距 ≤ 5mm"几何聚类，名字写成 `簇#1`、`簇#2`……，
报告里明确标「几何聚类，不是真实位号」；有位号时直接按位号分组。

**引脚间距怎么给**：两两算中心距，取出现次数最多的那个当**主间距**（排除首尾跨度那种大值），
同时给最小/最大中心距与最小边缘间距 —— 8 脚 SOIC 给的是 1.27mm，而不是 3.81mm 的跨度。

**焊盘到铜皮、到走线**（`drc.pad_clearance`，逐器件给最近距离，都是**边缘到边缘**）：

| 量什么 | 怎么算 |
|---|---|
| 到走线 | 焊盘外框到 ETCH 走线中心线的距离减去线宽/2；搜索半径默认 5mm，超出写 `>5` |
| 到铜皮 | 线宽 ≥0.5mm 的 ETCH 视为铜皮轮廓；闭合轮廓（铜皮外框）沿轮廓线算并减去轮廓线宽/2 |
| 落在铜皮内 | 焊盘中心在闭合铜皮轮廓内 → 只写「在铜皮内」，**不给数字**（那是同网络的平面/覆铜） |
| 相接 / 相交 | 距离 ≤0.001mm 的铜箔单列成"相接铜箔数"（多为同网络连线、热焊盘），**不计入间距** |
| 走线宽 / 长 | 顺带给出最近那条走线的线宽与线段长度：既回答"离它多远"，也回答"这条线多宽多长" |

判定依据取 `SpacingCSet.xml` 声明的 `LINE_TO_SMDPIN_SPACING`（走线到贴片焊盘）与
`SHAPE_TO_SMDPIN_SPACING`（铜皮到贴片焊盘）：实测低于声明值 → 不符合；BRD 没声明 → 只给实测值（信息）。
快照里没有网络信息，**同网络但不接触的铜箔**仍可能被算进间距，报告里明确写"请以 Allegro DRC 报告复核"。

夹具里的验证数字（`tools/selftest_analysis.py` 逐项核对）：U7 = SOIC-8，焊盘 1.5×0.6mm、
引脚间距 1.27mm、到走线 0.8684mm、到铜皮 3.5019mm；R60 = 0603，引脚间距 1.651mm、到走线 0.7938mm；
另有一个焊盘故意放在铜皮轮廓内 → 只标"在铜皮内"。

### 6.10 网络定位（按网络名查坐标 / 外框长宽）

DFM 窗口页眉的 **「网络定位」**：列 `.brd` 里的全部网络名，选一个就给出该网络的
坐标范围、中心、外框长宽、走线总长、线宽、层，并在画布上画出来（mm，板左下为原点）。

**数据口径（先看这段，别误解）**

| 数据 | 来源 | 说明 |
|---|---|---|
| 网络名 | `.brd` 归档 `objects/Net.xml` | 17.x 归档有；归档里没有时（如只存约束/物理信息的板）退回坐标文件的 `NET` 列；16.x 纯二进制再退回 `pstxnet.dat` 网表（只有名字，没有坐标） |
| 引脚/过孔坐标 | SKILL 导出 `evan_pads.csv` / `evan_holes.csv` 的 `NET` 列 | 现有 SKILL 脚本就会导出，NET 列不能为空 |
| 走线坐标 | SKILL 导出 `evan_traces.csv`（新版脚本） | 每段走线带 NET / 层 / 线宽 / 两端坐标；没有它就只能按引脚/过孔算外框 |
| `.brd` 内嵌快照 | 只有图形，**没有网络归属** | 所以"按网络名在快照里找坐标"做不到，工具不猜 |

没有带 NET 的导出文件时，窗口只列网络名，画布明确提示缺坐标并给出导出配方，
不会拿丝印/走线几何冒充某个网络。

> 实测：EQ-DFM 里多块量产 `*_FAB_BRD_*.brd` 的归档**只有约束 XML，没有
> `objects/Net.xml`**（网络数据库在二进制里，工具不猜）。这种情况窗口会提示
> "读不到网络名"；先用「生成 SKILL 脚本」导出一次，网络名就会从 `NET` 列带出来，
> 定位功能照常可用——这不是读取失败。

**界面**

- 左侧 **网络清单**：网络名 / 分类 / 引脚 / 过孔 / 走线 / 长度；筛选框支持 `*` `?`
  通配（如 `MIPI*`、`*D0*`），另有 `MIPI/MIPN`、只看差分对、只看有坐标三个快捷筛选；
- 右侧 **选中网络**：X/Y 范围、中心、外框 宽×高、走线总长、线宽范围、层、
  引脚/过孔/走线数量，下面是逐项坐标表；再下面是画布（蓝虚线=外框、红=引脚、
  绿=过孔、线=走线，可勾选显示板框作参照）；
- 按钮：导入坐标文件 / 生成 SKILL 脚本 / 刷新 / 导出当前网络 CSV。

![网络定位窗口（示例板）](docs/screenshots/ui_net_locate.png)

**怎么用**

1. Evan_DFM 导入 `.brd` → 点 **「网络定位」**；
2. 如果提示缺坐标：点 **「生成 SKILL 脚本」**，在 PCB Editor 里跑 `evan_pick_all`，
   把导出的 CSV 放回 `.brd` 同目录（或 `定位清单\allegro_bridge\`），回到窗口点 **刷新**；
3. 筛选/选中网络 → 看坐标与长宽；点 **导出当前网络 CSV** 可交给下游（CAM/仿真/复核）。

**限制**

- 只有引脚/过孔、没有 `evan_traces.csv` 时，外框是**引脚/过孔的包络**，不是铜箔实际范围，
  走线总长/线宽显示为空；这时的结论是"网络跨了多大范围"，不是"铜皮实际铺到哪"。
- 坐标是 Allegro 设计坐标（mm）；画布里的板框来自 `.brd` 快照，只作位置参照。

自检：`python tools/selftest_net_locate.py`（25 项，用最小 .brd 归档 + CSV，
不需要真实板和 Allegro）。

### 6.11 器件统计（按位号前缀分类 / 数量 / 封装 / 坐标）

DFM 窗口页眉的 **「器件统计」**：从 `.brd` 的位号清单统计"用了多少颗什么器件"，
按位号前缀分类，给出每类数量、占比和位号示例；明细里有封装、面别、X/Y、引脚数和
数据来源，可导出 Excel（汇总 / 分类 / 明细三个页签）。

**分类口径**（按位号前缀，大小写不敏感）

| 前缀 | 类别 | 前缀 | 类别 |
|---|---|---|---|
| R | 电阻 | RN / RP | 电阻排 |
| C | 电容 | CP | 钽电容 |
| L | 电感 | FB | 磁珠 |
| D | 二极管 | Q | 三极管/MOS管 |
| U / IC | IC芯片 | LED | 发光二极管 |
| J / P / CN / CON | 连接器/接插件 | SW / S | 开关 |
| F | 保险丝 | X / Y / OSC | 晶振 |
| T | 变压器 | B / BT | 电池 |
| TP | 测试点 | MH / H / FH | 安装孔 |

没列到的前缀不会被硬塞进"其它"：类别显示为 `未分类(前缀)`，在分类表里单独一行，
提醒补映射。位号按"前缀 + 数字"自然排序（R2 排在 R10 前面）。

**数据口径**

| 数据 | 来源 | 说明 |
|---|---|---|
| 位号（器件数） | `.brd` 归档 `objects/Part.xml` | 最可信；FAB 拼板图常常没有，缺失时退回导出文件 |
| 位号 + 坐标/面别 | `evan_components.csv`（SKILL） | X/Y/旋转/面别；没有 Part.xml 时就是主清单 |
| 引脚数 / 焊盘栈 | `evan_pads.csv`（SKILL） | 按位号去重引脚号；没有元件坐标时用焊盘中心推算并标注 |
| 封装 / 坐标 | Allegro Placement `place_txt.txt` | Placement 的封装列会补到"封装"；坐标优先级低于元件导出 |
| 封装 | `objects/PartDefn.xml` / `Gate.xml` | 防御式探测，只有明确带封装字段的节点才用 |
| 值 / 型号 / 料号 | **没有** | `Part.xml` 只有位号；Value/MPN 需要 BOM / ERP 数据 |

> 实测：`D:\Evan\EQ-DFM\26\S-860-000882\S-860-000882_FAB_BRD_Rev_A.brd`
> 归档里没有 `Part.xml`，仅凭 `.brd` 图形无法知道器件数量。窗口会明确提示
> "没有器件数据"，不会编造。用 PCB Editor 跑一次 SKILL 导出
> `evan_components.csv` + `evan_pads.csv`，或导入 Placement，再点「刷新」即可统计。

**界面**

- 顶部总览：器件总数 / 类别数 / TOP / BOTTOM / 面别未知 / 有坐标 / 引脚总数；
- 左侧分类表：全部 + 每个类别的数量、占比、位号示例，点一行筛选右侧；
- 右侧明细：位号 / 类别 / 封装 / 面别 / X / Y / 引脚数 / 来源，支持 `*` `?` 搜索；
- 「数据说明」列出 Part.xml 有无、来源文件、只在 Part.xml / 只在导出的差异和警告；
- 「导出 Excel」：汇总（指标+来源）、分类（类别/数量/占比/前缀/示例）、明细。

![器件统计窗口（示例板）](docs/screenshots/ui_component_stats.png)

**怎么用**

1. Evan_DFM 导入 `.brd` → 点 **「器件统计」**；
2. 有 `Part.xml` 直接出数；没有就点 **「生成 SKILL 脚本」**，在 PCB Editor 跑
   `evan_pick_all`（同时导出 `evan_components.csv` / `evan_pads.csv`），或
   `File > Export > Placement` 导出 `place_txt.txt`，回窗口点 **刷新**；
3. 看分类数量，点类别筛选，或「导出 Excel」交给采购/工艺。

自检：`python tools/selftest_component_stats.py`（24 项，合成最小 .brd + CSV，
覆盖全部前缀、去重、焊盘推算、无 Part.xml 兜底、无数据失败、Excel 三页签）。

---

## 7. 报告说明

程序会产出两类报告，别混在一起看：

### 7.1 Checklist 填写报告（主流程的产物）

`<checklist名>_checklist_report.xlsx`，7 个页签：

| 页签 | 内容 |
|---|---|
| `概览` | 单板 / BRD / 模板路径、工作表与表头行、列识别方式、板厂板型等级、填写人与时间，以及 PASS / VERIFY / FAIL / MANUAL / N/A / 规则命中 / 未覆盖 / 自动判定率 |
| `逐项判定` | 135 行全量：编号、类别、子类、级别、条目、判定、规则 id、依据 |
| `需人工确认` | MANUAL 项按类别分组，一条一条列出「要查什么」——拿这个当评审清单 |
| `不符合` | FAIL 项（附设计值 / 要求 / 建议） |
| `待验证` | VERIFY 项（需 DRC / SI 确认的） |
| `模板识别` | 表头行、列位置、每列的表头原文、识别警告、未覆盖条目清单 |
| `DFM 校验摘要` | 同一次运行里 DFM 制程能力比对的结论（交叉参考：checklist 里写 VERIFY 的项可以看这里） |

`<checklist名>_checklist_report.txt` 是同样的摘要，纯文本，便于贴邮件 / 评审记录。

### 7.2 DFM 报告（界面二「导出报告」）

| 页签 | 内容 |
|---|---|
| `Main` | 单板信息、问题汇总、可制造性评分、**数据覆盖率 / 判定 / 可否放行**、缺数据清单 |
| `Issue summary` | 按严重度汇总 |
| `Info` | 能力参数字典（线宽线距、钻孔、纵横比等） |
| `Issue1..N` | 每条问题的明细：编号、类别、规则、实测、要求、建议 |
| `数据来源` | 每个字段的取值与来源、数据校验警告 |

TXT 版结构：

```
==== DFM 制程能力分析报告 ====
板厂 / 板型 / 能力等级 / 铜厚 / 时间
问题总数 + 严重度分布
可制造性评分 / 风险等级 / 预估良率 / 结论
数据覆盖率 + 判定 + 可否放行 + 缺数据清单
数据来源（逐字段）
[数据校验] 警告
问题清单（编号、规则、实测、要求、建议）
```

---

## 8. 命令行参考

### 8.1 参数

| 参数 | 说明 | 默认 |
|---|---|---|
| `--brd PATH` | Allegro `.brd` 文件 | — |
| `--fill-checklist XLSX` | **主流程**：用 BRD 结果填写该 Checklist（传 `demo` 用内置演示模板） | — |
| `--template-info XLSX` | 只检查模板结构（工作表/表头行/列位置），不填写 | — |
| `--demo` | 内置演示板（PSE Board） | 关 |
| `--nets PATH` | JSON 网络表（替代 `--brd`） | — |
| `--checker NAME` | 填写人 | `BRD-AI` |
| `--factory` | `hongban` / `sihui` | `hongban` |
| `--board-type` | `rigid` / `flex` | `rigid` |
| `--capability` | `standard` / `automotive` / `extreme` | `standard` |
| `--copper` | 成品铜厚 um | `35` |
| `--copper-oz` | 成品铜厚 oz | 按 `--copper` 推算 |
| `--output DIR` | 输出目录 | `./output` |
| `--dfm JSON` | DFM 分析模式，传入设计参数 JSON | — |
| `--pick BRD` | 坐标定位模式：分析 BRD 并输出「数量 + 坐标」清单 | — |
| `--pick-files FILE...` | 坐标来源（SKILL 导出的 csv / place_txt.txt / Excellon .drl），不给则自动搜索 | — |
| `--export-skill DIR` | 只生成 Allegro SKILL 坐标导出脚本（.il/.scr/说明） | — |
| `--analyze BRD` | **BRD 分析与输出**：按 8 大类跑全部分析能力，并生成报告与可生成的制造文件 | — |
| `--analyze-files FILE...` | 分析用的 Allegro 导出文件：DRC 报告 / Placement / Excellon / 网表 / IPC-356 / 另一个 .brd（版本差异） | — |
| `--analyze-only ID...` | 只跑指定的能力 id 或类别（如 `drc.width`、`mfg.artwork`、`reports`） | — |
| `--analyze-skip ID...` | 跳过指定的能力 id 或类别 | — |
| `--analyze-formats LIST` | 报告格式，逗号分隔：`xlsx,txt,html,json,csv` | 全出 |
| `--list-capabilities` | 列出分析与输出引擎支持的全部能力（8 大类）后退出 | — |
| `--checklist PATH` | 旧版：读 Checklist 提取规则（不走新流程） | — |
| `--autofill PATH` | 旧版：填写输出路径 | `*_filled.xlsx` |
| `--gui` | 启动图形界面 | — |

### 8.2 常用示例

```powershell
# 填写 Checklist（推荐用法）
python main.py --fill-checklist .\Layout_Checklist_Rev1.0.xlsx `
               --brd .\860-000678_FAB_BRD_Rev_A.brd --output .\out

# 换模板后先自检
python main.py --template-info .\Layout_Checklist_Rev2.0.xlsx

# 演示（不需要 BRD 和模板）
python main.py --fill-checklist demo --output .\out

# 软板 + 四会 + 极限等级
python main.py --fill-checklist tpl.xlsx --brd flex.brd `
               --board-type flex --factory sihui --capability extreme

# 只做 DFM 分析（不读 BRD，纯参数；仓库自带示例 dfm_design_sample.json）
python main.py --dfm dfm_design_sample.json --board-type rigid --factory hongban

# 坐标定位：先生成 SKILL 导出脚本，导完坐标再出定位清单
python main.py --export-skill .\out\allegro_bridge
python main.py --pick .\860-000678_FAB_BRD_Rev_A.brd `
               --pick-files .\out\allegro_bridge\evan_components.csv `
                            .\out\allegro_bridge\evan_holes.csv `
               --output .\out\定位清单

# 只有 Excellon 钻孔文件也能定位孔（没有元件坐标）
python main.py --pick .\board.brd --pick-files .\board.drl --output .\out\定位清单

# BRD 分析与输出：没有 Allegro 导出文件也能跑（能算的算，算不了的写明缺什么）
python main.py --analyze .\board.brd --output .\out

# 把板厂/SMT/测试厂给回来的导出文件一起喂进去，结论会完整很多
python main.py --analyze .\board.brd `
               --analyze-files .\drc.rpt .\place_txt.txt .\board.drl `
                               .\pstxnet.dat .\board.ipc .\board_RevA.brd `
               --output .\out\分析报告

# 只要制造文件（Gerber / 钻孔参数 / 坐标 / BOM）与相关能力
python main.py --analyze .\board.brd --analyze-only manufacturing

# 只跑某一项，边调边看
python main.py --analyze .\board.brd --analyze-only drc.clearance

# 看这个引擎一共支持哪些能力
python main.py --list-capabilities
```

> 旧版「读 BRD → 生成 SKILL 约束脚本 + 约束报告」的整条流水线仍在（`python main.py --brd xxx.brd`），
> 但界面主流程已经只做 Checklist 填写 + DFM 报告。

### 8.3 DFM 参数 JSON 示例

```json
{
  "board_name": "860-000678",
  "min_line_width": 0.1001,
  "min_spacing": 0.127,
  "min_mech_drill": 0.2032,
  "min_annular_ring": 0.1016,
    "min_annular_ring_laser": 0.0762,
  "board_thickness": 1.6,
  "test_pad_diameter": 0.8,
  "component_to_edge": 5.0,
  "process_edge_mm": 8.0,
  "panel_size": [220, 180],
  "pth_to_pth_spacing": 0.45,
  "copper_oz": 1.0
}
```

字段名与 6.7 的参数表一致；`board_size` / `panel_size` 用两元素数组。

---

## 9. 项目结构

界面代码按**功能**拆成独立文件，一个文件只做一件事；两个程序共用 `src/`，入口各一个。

```
brd_extract/
├── evan_brd.py             # 界面一入口 (Checklist)
├── evan_dfm.py             # 界面二入口 (DFM 分析, 独立 exe)
├── gui.py                  # 兼容入口：转发到 src/ui/，保留 python gui.py 的用法
├── main.py                 # 命令行入口 + 流水线
├── Evan_BRD.spec           # 界面一打包配置 -> dist\Evan_BRD.exe
├── Evan_DFM.spec           # 界面二打包配置 -> dist\Evan_DFM.exe
├── build_evan_brd.bat      # 一键打包脚本 (两个 exe 一起出)
├── evan.ico / evan.png     # 图标
├── dfm_design_sample.json  # DFM 参数示例（--dfm 用）
├── TUTORIAL.md             # 本文档
├── docs/screenshots/       # 界面截图
├── config/
│   ├── dfm_capability_hongban.yaml   # 红版制程能力库
│   ├── dfm_capability_sihui.yaml     # 四会制程能力库
│   ├── checklist_auto_rules.yaml     # ★ Checklist 模板识别 + 判定规则库（41 条）
│   └── checklist_rules.yaml          # 约束规则库（8 组）
├── src/
│   ├── ui/                 # ---- 界面层：按功能分文件 ----
│   │   ├── __init__.py     # 导出 MainWindow / DFMDialog / DFM_FIELDS
│   │   ├── theme.py        # 配色 / 字体 / ttk 样式 / 卡片 / 日志控件
│   │   ├── widgets.py      # 通用控件：表单行 / 下拉 / 表格 / 滚动区
│   │   ├── prefs.py        # 界面偏好读写（%APPDATA%）
│   │   ├── paths.py        # 程序目录 / 默认输出目录（兼容打包）
│   │   ├── fields.py       # 兼容层: 再导出 dfm_inputs 的定义
│   │   ├── main_panels.py  # 页眉 / 状态栏 / 底部条 / 三张卡片
│   │   ├── main_window.py  # 主窗口：组装 + 生命周期 + 后台任务
│   │   ├── dfm_dialog.py   # 界面二主窗口：组装 + 交互（可独立运行）
│   │   ├── dfm_params.py   # 界面二左栏：参数表单（BRD 项默认隐藏）
│   │   ├── dfm_results.py  # 界面二右栏：制程能力比对各表
│   │   ├── dfm_locate.py   # 坐标定位窗口
│   │   ├── component_stats_dialog.py # 器件统计窗口（分类/数量/封装/坐标）
│   │   └── net_locate_dialog.py # 网络定位窗口（按网络名查坐标/长宽）
│   ├── ui_theme.py         # 兼容层：转发到 src/ui/theme.py
│   ├── checklist_template.py   # ★ 模板识别：找 sheet / 找表头 / 列映射 / 读条目
│   ├── checklist_auto_rules.py # ★ 规则匹配 + 判定（compare/dfm/count/attest）
│   ├── checklist_pipeline.py   # ★ 主流程：6 步串起来（无 tkinter）
│   ├── checklist_report.py     # ★ Checklist 填写报告（Excel 7 页 + TXT）
│   ├── checklist_auto_fill.py  # 按判定结果写 xlsx（只填空白格）
│   ├── checklist_reader.py     # 旧版读取器（固定列，仅供 --checklist 旧接口）
│   ├── brd_raw.py          # BRD 二进制扫描（单位/钻孔表/层/焊盘栈/说明）
│   ├── brd_components.py   # 器件位号清单（objects/Part.xml，数量+分类）
│   ├── dfm_inputs.py       # DFM 48 项参数表 + BRD→设计参数映射 (界面/CLI 共用)
│   ├── brd_design.py       # 约束 XML + 二进制合成 → 设计参数
│   ├── dfm_engine.py       # 制程能力比对 + 覆盖率
│   ├── dfm_rules.py        # 问题构建 / 严重度 / 风险评分
│   ├── dfm_report.py       # DFM 报告（Excel / TXT）
│   ├── pcb_reader.py       # 旧版 BRD 解析（网络/层）
│   ├── signal_classifier.py# 16 类信号识别 + 差分对
│   ├── rule_engine.py      # 约束计算
│   ├── design_geometry.py  # 从内嵌快照算板框尺寸
│   ├── pad_metrics.py      # 引脚间距(中心距)/焊盘间距(边缘距) 计算与提取
│   ├── pad_geometry.py     # ★ 焊盘几何: 尺寸/引脚间距/器件分组 + 到铜皮·走线距离
│   ├── silkscreen.py       # 丝印线宽 + 丝印到板框/到器件本体距离
│   ├── pads.py             # 焊盘提取 + 钢网层圆焊盘→测试点
│   ├── pick.py             # 坐标解析 / 不符合项定位 / 定位清单输出
│   ├── net_locate.py       # ★ 网络定位: 网络名 + 带 NET 坐标导出 → 坐标/外框/长宽
│   ├── component_stats.py  # ★ 器件分类统计: Part.xml/导出文件 → 类别/数量/封装/坐标
│   ├── allegro_skill.py    # 生成 Allegro SKILL 坐标导出脚本
│   ├── skill_generator.py  # SKILL 约束脚本生成（CLI 旧流程用）
│   └── report_generator.py # 约束报告（Excel / TXT）
├── dist/                   # 打包产物 Evan_BRD.exe / Evan_DFM.exe
└── output/                 # 运行输出（默认目录）
```

### 9.1 界面文件的调用关系

```
界面一: evan_brd.py / gui.py
        └── src/ui/main_window.py ── 主窗口 (只有 Checklist)
              ├── main_panels.py        (页眉/状态栏/底部条/卡片)
              │     └── theme.py + widgets.py
              └── src/checklist_pipeline.py  (填写 Checklist，跑在后台线程)
                    ├── checklist_template.py  (认模板)
                    ├── checklist_auto_rules.py(认规则 + 判定)
                    ├── checklist_auto_fill.py (写 xlsx)
                    └── checklist_report.py    (写报告)

界面二: evan_dfm.py ── 独立程序
        └── dfm_dialog.py ── DFM 主窗口 (自己导入 BRD)
              ├── dfm_params.py      (左栏参数表单; BRD 项默认隐藏)
              │     └── fields.py    (48 项定义)
              ├── dfm_results.py     (右栏结果表格 +「坐标定位」按钮)
              ├── dfm_locate.py      (坐标定位窗口)
              │     └── src/pick.py + src/allegro_skill.py
              ├── component_stats_dialog.py (器件统计窗口)
              │     └── src/component_stats.py + src/brd_components.py + src/pick.py
              └── net_locate_dialog.py (网络定位窗口)
                    └── src/net_locate.py + src/allegro_skill.py
```

线程模型固定为「**后台线程只算、主线程只画**」：`checklist_pipeline` / `dfm_engine` 不碰 tkinter，
日志通过 `root.after()` 回主线程；界面回调里的异常会写进日志区和 `Evan_BRD_crash.log`。

### 9.2 模块职责速查

| 想改什么 | 去哪 |
|---|---|
| 界面配色 / 字体 / 按钮样式 | `src/ui/theme.py` |
| 主窗口布局 / 按钮 | `src/ui/main_panels.py` |
| 主窗口逻辑 / 后台调度 | `src/ui/main_window.py` |
| 填写流程的步骤 / 日志文案 | `src/checklist_pipeline.py` |
| 判定规则 / 模板列别名 | `config/checklist_auto_rules.yaml` |
| 模板识别逻辑 | `src/checklist_template.py` |
| 判定状态与写表方式 | `src/checklist_auto_fill.py` |
| 填写报告版式 | `src/checklist_report.py` |
| 界面二入口 / 窗口 | `evan_dfm.py` / `src/ui/dfm_dialog.py` |
| BRD 参数行的隐藏/展开 | `src/ui/dfm_params.py`（`_visible` / `_render_fields`） |
| 界面二左侧参数 | `src/ui/dfm_params.py`（表单/来源标签/隐藏 BRD 行）+ `src/dfm_inputs.py`（字段与映射） |
| 界面二右侧表格 | `src/ui/dfm_results.py`（制程能力比对） |
| 网络定位窗口 / 筛选 / 画布 | `src/ui/net_locate_dialog.py` |
| 网络坐标聚合 / 外框长宽口径 | `src/net_locate.py` |
| 器件统计窗口 / 分类筛选 / Excel 导出 | `src/ui/component_stats_dialog.py` |
| 器件分类 / 多来源合并 / 引脚去重 | `src/component_stats.py` |
| 板厂能力数值 | `config/dfm_capability_*.yaml` |
| DFM 判定项与阈值 | `src/dfm_engine.py` |
| DFM 报告版式 | `src/dfm_report.py` |
| BRD 提取 | `src/brd_raw.py` / `src/brd_design.py` |
| 器件清单（数量/位号） | `src/brd_components.py`（读 `objects/Part.xml`） |
| 引脚间距 / 焊盘间距 | `src/pad_metrics.py`（圆/矩形/旋转矩形几何）+ `src/pad_geometry.py`（器件分组与主间距） |
| 焊盘到铜皮 / 到走线距离 | `src/pad_geometry.py: measure_pad_clearance`（铜箔栅格索引 + 闭合铜皮轮廓） |
| 丝印线宽 / 丝印到成型线 / 丝印到器件 | `src/silkscreen.py`（SILKSCREEN 图形 + 板框/PLACE_BOUND 几何） |
| 测试点 / 焊盘 | `src/pads.py`（PAD/PASTEMASK/SOLDERMASK 圆焊盘 → 测试点） |
| 哪个值来自 BRD、哪个是示例 | `src/dfm_inputs.py` + `src/ui/dfm_params.py`（标签）|
| 板框尺寸怎么算 | `src/design_geometry.py`（内嵌快照 + Draw Size 标定） |
| 坐标来源怎么解析 / 怎么匹配不符合项 | `src/pick.py` |
| SKILL 导出脚本长什么样 | `src/allegro_skill.py` |
| 定位清单的 Excel 版式 | `src/pick.py: write_report()` |
| 定位窗口的交互 | `src/ui/dfm_locate.py` |
| 判定用哪些值 | `src/dfm_engine.add_check()` 只收到已确认值, 示例值在 `read_design()` 就被拦掉 |

---

## 10. 打包发布

### 10.1 构建

```powershell
# 方式一：双击（含环境检查与清理, 两个 exe 一起出）
build_evan_brd.bat

# 方式二：命令行（要出几个就 build 几个）
python -m PyInstaller --clean --noconfirm Evan_BRD.spec   # 界面一
python -m PyInstaller --clean --noconfirm Evan_DFM.spec   # 界面二
```

产物：`dist\Evan_BRD.exe`（界面一）与 `dist\Evan_DFM.exe`（界面二），各约 13~14 MB，
单文件、无控制台窗口。两个程序可以只分发一个，也可以都放桌面上。

### 10.2 打包配置要点

- `config/*.yaml`、`evan.ico`、`evan.png` 作为 datas 打进去（含 `checklist_auto_rules.yaml`）；
- 两个 spec 各自列 `hiddenimports`：界面一含 `main_panels / main_window / checklist_*`，界面二含 `dfm_dialog / dfm_params / dfm_results / dfm_locate / component_stats_dialog / net_locate_dialog`；共用 `src.brd_raw`、`src.brd_design`、`src.dfm_engine`、`src.component_stats`、`src.net_locate` 等——这些模块按需 import，不显式列出容易漏包；
- `excludes` 排除 `numpy` / `lxml` / `PIL`，减小体积；
- `console=False`，界面一异常写 `dist\Evan_BRD_crash.log`，界面二写 `dist\Evan_DFM_crash.log`；
- 打包前若 exe 正在运行会占用文件，`build_evan_brd.bat` 会先结束这两个进程再构建。

### 10.3 产物验证清单

```powershell
# 1. 文件存在且时间正确
Get-ChildItem dist\Evan_BRD.exe

# 2. 启动窗口（标题应为 Evan_BRD - 分析BRD (Cadence Allegro)）
Start-Process dist\Evan_BRD.exe

# 3. 用打包版跑一次命令行, 验证模块/配置都打进去了 (最关键的一步)
.\dist\Evan_BRD.exe --fill-checklist .\Layout_Checklist_Rev1.0.xlsx `
                    --brd .\860-000678_FAB_BRD_Rev_A.brd --output .\dist\_cli_out
Get-ChildItem .\dist\_cli_out          # 应有 filled.xlsx + report.xlsx + report.txt
Get-Content .\dist\Evan_BRD_cli.log    # 打包版没有控制台, 输出都在这里

# 4. 确认关键资源已打进包内
python -m PyInstaller.utils.cliutils.archive_viewer -l dist\Evan_BRD.exe |
    Select-String "checklist_auto_rules|dfm_capability|evan.ico"

# 5. 打包版界面二: 双击 Evan_DFM.exe (或带 BRD 路径启动)
Get-ChildItem dist\Evan_DFM.exe
Start-Process dist\Evan_DFM.exe -ArgumentList .\860-000678_FAB_BRD_Rev_A.brd
#    窗口标题: Evan_DFM - DFM 分析 (制程能力比对)
#    左栏只剩需要人工补的项 (BRD 读到的 16 项已隐藏), 状态栏给出 "BRD 数据 N 项"
#    点「执行分析」: 右栏出现 PASS/FAIL/WARN 逐项比对结果

# 6. 坐标定位 (打包版, 无控制台, 输出看 Evan_BRD_cli.log 和输出目录)
.\dist\Evan_BRD.exe --export-skill .\dist\_pick\bridge
.\dist\Evan_BRD.exe --pick .\860-000678_FAB_BRD_Rev_A.brd `
    --pick-files .\dist\_pick\evan_holes.csv --output .\dist\_pick\out
Get-ChildItem .\dist\_pick\out    # 应有 定位清单.xlsx/.csv/.txt (+ locate.scr)

# 7. 网络定位 (打包版界面二): 导入 BRD -> 点「网络定位」
#    左栏应列出 .brd 的网络名; 有 evan_pads/holes/traces.csv 时选中网络能看到
#    X/Y 范围、中心、外框 宽×高、走线总长, 画布按 mm 画出图形。
#    没有带 NET 的导出文件时应提示"缺坐标", 而不是空表或假坐标。

# 8. 器件统计 (打包版界面二): 导入 BRD -> 点「器件统计」
#    有 objects/Part.xml 时应显示总数/分类/明细; 没有时应提示"没有器件数据",
#    生成 SKILL 或导入 evan_components.csv + evan_pads.csv 后刷新即出数;
#    点「导出 Excel」应得到 汇总 / 分类 / 明细 三个页签。
```

> 本次改动后的实机核验方式：用 `PrintWindow` 抓客户区截图排除"画了个空表"，
> 再用运行期计数确认真实带入 —— 打开 DFM 窗口后断言 `BRD` 标签 16 项、`手工` 0 项，
> 执行分析后断言右侧出现彩色 PASS/FAIL/WARN 行。打包版同样能定位到 DFM 窗口（本次已抓图核对 1238×847、非空白）。

![打包版启动实机](docs/screenshots/ui_exe.png)

上图是**打包后的 exe 实机运行**（不是源码窗口）：恢复上次路径 → 点「填写 Checklist 报告」→
六步跑完 → 状态栏给出 `PASS / VERIFY / FAIL / 需人工 / N/A` 计数，右侧日志是同一份内容。

运行时注意：

- **打包版也支持命令行**：带参数启动就走 CLI（输出写 `Evan_BRD_cli.log`），不带参数才开界面；
- **输出目录**在打包后是 `<exe 所在目录>\output`（不是临时解压目录，退出不会丢文件）；
- 界面偏好写在 `%APPDATA%\Evan_BRD\ui_state.json`，下次打开自动带出上次的 BRD/模板/输出目录；
  这个文件手工改坏也没关系：读不出来就当没有（程序不会崩），删掉即恢复默认；
- 界面日志同时落盘到 `<exe 目录>\Evan_BRD_ui.log`（每次启动重写一遍）。窗口版没有控制台，
  反馈问题时把这个文件连同 BRD/模板一起发过来，就能看到当时到底走到哪一步；
- 规则库可外挂：`<exe 目录>\config\checklist_auto_rules.yaml` 优先于包内版本；
- 若杀毒软件拦截单文件 exe，加白名单即可（PyInstaller onefile 常见误报）。

---

## 11. 常见问题

**Q：为什么覆盖率只有 20%–30%，是不是没解析出来？**

不是解析失败，是 `.brd` 里确实没有。阻焊、丝印、器件间距、测试点、拼板这些都要图形数据。程序如实报覆盖率，而不是把没查的当通过。（板框尺寸已经能读出来了，见 4.2。）

**Q：DFM 说 0.199mm 钻孔超能力，到底几个、在哪？**

分两步：数量不用导出——`.brd` 的钻孔表就带每个孔径的数量；坐标要导一次——`--export-skill` 生成 SKILL 脚本，在 PCB Editor 里跑完后再 `--pick`，`定位清单.xlsx` 会列出"孔径 = 0.1992mm 的孔 N 个"和每个孔的 X/Y、所在网络。

**Q：我没有 Allegro，能不能只要数量？**

可以。不带任何坐标来源直接 `--pick`：清单里"孔数"一列照常给（来自 `.brd` 钻孔表），"坐标"列写「无坐标」。工具不会编坐标。

**Q：SKILL 跑完 `evan_components.csv` 是空的 / 日志报错？**

说明某个属性名在你的 Allegro 版本里不一样。把 `evan_pick.log` 发回来按报错改一行即可；脚本里每一步都写了条数，能立刻看出是元件、过孔还是引脚那一段失败。注意要用 PCB Editor 打开设计，Free Viewer 不能跑 SKILL。

**Q：为什么有些不符合项没有坐标？**

孔壁间距、PTH 纵横比、线宽线距、阻焊、丝印这些判定依赖铜箔/阻焊/丝印的形状，不是靠孔位或位号坐标就能定位的，需要 CAM/Gerber。清单里会写明「需要图形几何 (CAM/Gerber)」，不会拿最近的元件坐标冒充问题位置。

**Q：器件数量为什么显示「读不到器件」？**

因为这块 `.brd` 里根本没有 `objects/Part.xml`（FAB 拼板图、纯板框图纸常见）。器件的位号和数量存在设计的器件对象里，
不在丝印/装配图形里；本工具不会用"数了几条装配线"去冒充器件数。要看器件数，请用带器件的设计源文件（`.brd` 原始设计或
Allegro 导出的器件清单）。

**Q：测试点为什么显示「缺数据」？钢网层圆焊盘不是能读吗？**

钢网层（`PASTEMASK`）上的圆焊盘确实按测试点算，`src/pads.py` 已经写好识别与直径判定；
但这 78 块样本 `.brd` 的**内嵌快照里没有 PAD / PASTEMASK / SOLDERMASK 图形**（只有丝印、装配、ETCH、
板框、ROUTE KEEPIN），导出的 JSON 里连一条焊盘几何都没有。硬拿丝印块或 `PIN` 标记当焊盘，
会算出假直径，所以界面如实写"缺数据"。要真识别测试点，需要 Gerber（钢网层开窗）或 Allegro 的焊盘坐标报告。

**Q：那「焊盘尺寸 / 引脚间距 / 焊盘到铜皮·走线距离」凭什么能出数？**

同样是上面那条限制：**快照里没有焊盘几何就出不了数**，工具不会编。能出数只有两种情形（见 6.9）：
① 你跑了 SKILL 脚本、把 `evan_pads.csv` 一起喂进来 —— 此时焊盘尺寸、位号、引脚间距都来自设计数据库，最准；
② 这块板的快照里恰好带 `PAD/PIN`、`PASTEMASK`、`SOLDERMASK` 图形 —— 此时能用量到的焊盘外框出尺寸和间距，
但**没有位号**，只能按几何聚类成 `簇#n`，报告里会写明"不是真实位号"。
两种情况都会继续用快照里的 ETCH 图形量"焊盘到铜皮 / 到走线"的距离（只算未相连的铜箔，相接的单列）。

**Q：「丝印到器件」为什么只算 0.43mm / 0mm？**

快照里没有焊盘，器件本体只能用 `PLACE_BOUND` / `ASSEMBLY` 轮廓代替；组件**自己的**丝印天然贴着自己的本体
（距离恒为 0，没有意义），所以只量**板级丝印**（`BOARD GEOMETRY/SILKSCREEN_*`）到器件本体。
量出 0mm 表示板级丝印与某器件本体轮廓重合/相切，可能是丝印压件，也可能是装配框与板框重合，需要人看一眼。

**Q：NPTH 孔到板框成型线为什么算不出来？**

`.brd` 内嵌快照只有图形（板框、丝印、装配、ROUTE KEEPIN），没有**每个孔的坐标**；钻孔表只给孔径、跨度、数量。
孔壁到成型线必须逐个孔量到板框的距离，所以要钻孔坐标（CAM / NC Drill 文件）。界面里这一项会明确写"缺孔坐标"，
不填假值；等接了钻孔文件后再自动算。

**Q：板厚为什么提示「需核对」？**

板厚取自二进制里的制程说明文本（`PCB` / `OVERALL` 关键字行），不是结构数据。程序给了范围过滤（0.3–10mm）和低置信标记，但最终要人确认。

**Q：孔环显示 FAIL，是不是设计真有问题？**

先看能力库取值。红版能力库中机械孔单边孔环按 6mil（0.1524mm）计，而实测板子常在 0.0762–0.127mm 之间。这属于**能力数据与设计惯例不一致**，需要用板厂最新能力表核对 `config/dfm_capability_hongban.yaml` 对应行，而不是直接判定设计超标。

**Q：`.brd` 里 `MIN_NECK_WIDTH=2.0` 被丢掉了？**

是数据校验拦截的：该值单位应为 mil（0.0508mm），但字段声明可能是 mm，量级不合理。被拦截时会在「数据校验」里留警告，不会静默吞掉。

**Q：没有 Allegro 授权能用吗？**

能。整个流程不调用 Allegro，纯文件解析。反过来说，也因此读不到图形几何。

**Q：换了新版 checklist.xlsx，程序认不出来怎么办？**

先点「检查模板」看提示框。三种常见情况：

| 提示 | 处理 |
|---|---|
| 「改用最后一个工作表」 | 把 sheet 名改成含 `check`/`checklist`，或加进 `template.sheet_candidates` |
| 「列识别失败 / 缺少 xxx 列」 | 新表头不在别名表里，把列名加进 `template.columns` 对应项（见 5.4） |
| 「固定列兜底」 | 前 15 行没找到表头，确认表头行是否上移/下移过多，必要时调大 `header_scan_rows` |

**Q：填出来全是 MANUAL，规则没生效？**

看「模板识别」页的「未覆盖条目」。若所有条目都没命中，八成是列映射错了（条目列指到了空白列）。
先 `python main.py --template-info 你的文件.xlsx` 确认哪一列是「条目」，再对规则库。

**Q：DFM 分析去哪了？界面一为什么没有 DFM 按钮了？**

DFM 搬成了独立程序 `dist\Evan_DFM.exe`（界面二）。界面一现在只做 Checklist 填写，
底部只剩 填写 Checklist 报告 / 演示运行 / 打开输出目录。两个程序共用同一套解析与判定代码，
但**各自导入自己的 BRD**：界面二不需要先开界面一，界面一也不再往界面二传路径。

**Q：界面二左栏怎么少了一大半参数？**

BRD 能读到的 16 项（层数、线宽、线距、孔径、孔环、板厚、板尺寸、线路到板边等）自动带入后**默认隐藏**，
判定照用。要核对或覆盖就勾参数区右上角「显示 BRD 带入项 (12)」；改过的值会变成"手工"，
参与判定并覆盖 BRD 值。

**Q：界面二那些框，为什么还要我填？**

界面上只剩 BRD 给不出的项：

- 蓝色 **手工** —— 你填的 / 改过的值，参与判定；
- 橙色 **示例** —— 表单预填值，没确认过，**不参与判定**（点「确认全部示例值」或自己改一下即生效）；
- 灰色 **待填** —— BRD 里确实没有这类数据。

`.brd` 只有约束对象（线宽/线距/孔径/孔环）、网络、层、钻孔表，**没有图形几何**。
板框、阻焊、丝印、拼板、覆盖膜这些是画出来的图形层，程序不会去猜；
覆盖膜三项（开窗间距 / 到焊盘 / 到线路）属软板图形，要 CAM 或图纸给。
你把实测值填进去，程序就拿它和 `config/dfm_capability_*.yaml` 比"是否超过"。

**Q：点上按钮没反应 / 程序闪退？**

窗口版没有控制台，所以先看日志：

- 界面一：`<exe 目录>\Evan_BRD_ui.log`（每次启动重写）+ `Evan_BRD_crash.log`；
- 界面二：`<exe 目录>\Evan_DFM_crash.log`（崩了才有）；参数/规则问题多看界面里的状态栏与提示框。

把日志（和当时选的 BRD/模板）一起给开发即可定位。

**Q：打包后想改规则，必须重新打包吗？**

不用。把 `config\checklist_auto_rules.yaml` 放在 `Evan_BRD.exe` 同目录的 `config\` 下，
程序优先读外部这份（`src/checklist_template.py` 的 `config_candidates`）；
想恢复内置规则，删掉外部文件即可。

**Q：打包后提示找不到 `config/xxx.yaml`？**

检查 `Evan_BRD.spec` 的 `datas` 是否包含该文件，并确认模块用的是相对程序目录的配置路径。改完必须重新打包。

**Q：换板厂能力表怎么办？**

直接改 `config/dfm_capability_<factory>.yaml`（`meta.source_files` 记录来源 Excel），结构见 6.2。等级增删同步改 `levels` / `level_labels` 即可，界面下拉会自动跟随。

---

## 12. BRD 分析与输出（8 大类能力）

主流程（第 5 章）回答的是「Checklist 这一行该填什么」，DFM 模块（第 6 章）回答的是「这块板能不能做」。本节是第三个引擎，回答的是**「这块板的设计与制造数据，我能替你查出什么、导出什么」**——把 Allegro PCB Editor 里散落在 8 个菜单下的分析与输出功能，做成一套不依赖 Allegro 授权的批处理能力。

入口有三个，共用同一套结论：

```powershell
python main.py --list-capabilities                                  # 看支持什么
python main.py --analyze .\board.brd --output .\out                 # 跑全部
python main.py --analyze .\board.brd --analyze-only drc.clearance   # 只跑一项
```

界面上是主窗口底部的 **「BRD 分析与输出」** 按钮（界面三，见 12.5）。

### 12.1 四类数据来源：能算什么，取决于数据在哪

这个引擎的每一项能力都打着一个**数据来源**标签。看到标签就知道结论的可靠程度，也知道缺什么该去哪里补：

| 标签 | 含义 | 可靠度 |
|---|---|---|
| `BRD 二进制/归档直读` | 直接读 `.brd` 的二进制段与内嵌 XML 归档（层名、钻孔表、约束对象、网络名、位号） | 高 |
| `BRD 内嵌设计快照几何` | 读 `.brd` 内嵌的设计快照图形（板框、ETCH、丝印、装配、钢网层的线/弧/圆） | 中，且**无网络归属** |
| `Allegro 导出文件` | 需要你把 Allegro 导出的报告/坐标文件一起喂进来 | 高（但取决于导出是否规范） |
| `需 Allegro/Sigrity 授权模块` | 本工具**产不出来**，只能告诉你缺什么、给出导出配方 | — |

### 12.2 65 项能力一览

<details>
<summary>点开看全部 65 项（id / 中文名 / 数据来源）</summary>

**① 设计规则检查 (DRC)**

| id | 能力 | 数据来源 |
|---|---|---|
| `drc.check` | DRC 违规汇总 | Allegro 导出文件 |
| `drc.clearance` | 间距检查 | 内嵌快照几何 |
| `drc.width` | 线宽检查 | 内嵌快照几何 |
| `drc.via` | 过孔检查 | 二进制/归档直读 |
| `drc.short` | 短路检查 | 需 Allegro 授权模块 |
| `drc.open` | 开路检查 | 需 Allegro 授权模块 |
| `drc.unconnected` | 未连接检查 | 需 Allegro 授权模块 |
| `drc.constraint` | 约束检查 | 二进制/归档直读 |
| `drc.pad_clearance` | 焊盘到铜皮与走线间距 | 焊盘几何 + 内嵌快照几何 |

**② PCB 物理结构分析**

| id | 能力 | 数据来源 |
|---|---|---|
| `physical.board_geometry` | 板框与板边 | 内嵌快照几何 |
| `physical.stackup` | 层叠结构 | 二进制/归档直读 |
| `physical.cross_section` | 叠层截面 | 二进制/归档直读 |
| `physical.copper` | 铜箔分析 | 内嵌快照几何 |
| `physical.via_structure` | 过孔结构 | 二进制/归档直读 |
| `physical.shape` | 铜皮与平面 | 内嵌快照几何 |
| `physical.placement` | 元件布局 | 二进制/归档直读 |
| `physical.pad_geometry` | 焊盘尺寸与引脚间距 | 焊盘几何 + 内嵌快照几何 |

**③ SI 与电气分析**

| id | 能力 | 数据来源 |
|---|---|---|
| `electrical.impedance` | 特性阻抗 | 内嵌快照几何 |
| `electrical.capacitance` | 寄生电容 | 内嵌快照几何 |
| `electrical.inductance` | 寄生电感 | 内嵌快照几何 |
| `electrical.resistance` | 网络电阻 | 内嵌快照几何 |
| `electrical.delay` | 传播延迟 | 内嵌快照几何 |
| `electrical.crosstalk` | 串扰 | 内嵌快照几何 |
| `electrical.signal_integrity` | 信号完整性分析 | 需 Sigrity 授权 |
| `electrical.net_analysis` | 网络电气参数 | 二进制/归档直读 |

**④ 制造文件输出**

| id | 能力 | 数据来源 |
|---|---|---|
| `mfg.artwork` | Artwork 光绘 (Gerber RS-274X) | 内嵌快照几何 |
| `mfg.nc_drill` | NC 钻孔文件 (.drl) | 二进制/归档直读 |
| `mfg.nc_route` | NC 铣外形路径 | 内嵌快照几何 |
| `mfg.ipc2581` | IPC-2581 数字产品模型 | 需 Allegro 授权 |
| `mfg.odbpp` | ODB++ 制造数据 | 需 Allegro 授权 |
| `mfg.ipc356` | IPC-D-356 测试网表 | 导出文件 |
| `mfg.pick_place` | 贴片坐标文件 | 导出文件 |
| `mfg.bom` | BOM 物料清单 | 二进制/归档直读 |

**⑤ PCB 报告输出**（11 项，每项同时出 RPT + CSV + HTML 三种格式）

`report.board` / `report.component` / `report.pin` / `report.net` / `report.via` / `report.etch` / `report.layer` / `report.unconnected` / `report.drc` / `report.constraint` / `report.testpoint`

**⑥ SMT 贴装与装配分析**

`smt.placement` · `smt.rotation` · `smt.refdes` · `smt.bom` · `smt.assembly_drawing` · `smt.silkscreen` · `smt.testpoint` · `smt.variant`

**⑦ 机械结构与 ECAD/MCAD 数据交换**

`mcad.dxf`（真出 DXF R12）· `mcad.idf` · `mcad.idx` · `mcad.step` · `mcad.creo`

**⑧ 数据库与设计数据导出**

`db.netlist` · `db.symbol_spreadsheet` · `db.placement` · `db.parameters` · `db.annotation` · `db.techfile` · `db.design_difference` · `db.report_export`

</details>

### 12.3 六种结论状态，以及「缺数据 ≠ 通过」怎么落地

每一项能力都返回**六种状态之一**，报告里逐项给出结论、关键指标、明细表、出处与置信度：

| 状态 | 含义 | 什么时候出现 |
|---|---|---|
| 通过 | 有数据，且判定合格 | 例如最小线宽 ≥ 约束最小线宽 |
| 不符合 | 有数据，且判定不合格 | 例如实测净间距 < 声明的线距约束 |
| 需关注 | 有数据，但需要人确认 | 例如 Gerber 里有无宽度的图形、非正交贴装角 |
| 信息 | 只报事实，不做判定 | 叠层截面、铜箔统计、电气估算 |
| 数据不足 | **算不了**，并写清缺什么、怎么补 | 没有 DRC 报告时的短路检查 |
| 未运行 | 本轮被 `--analyze-skip` 跳过 | — |

三条硬纪律（自检脚本 `tools/selftest_analysis.py` 会逐项校验）：

1. **`unavailable` 必须带 `missing` + `howto`**——说清楚缺哪个数据、在 Allegro 的哪个菜单导出。不允许「静默略过」。
2. **给出判定的项必须有指标或明细支撑**——不允许只写一句「通过」。
3. **声称生成的文件必须真的在磁盘上且非空**——不允许「报了文件名但文件没写出来」。

### 12.4 真的会生成文件的那些能力

以下产物是**真写盘**的（不是占位符），路径都在输出目录下：

| 产物 | 位置 | 说明 |
|---|---|---|
| Gerber RS-274X | `<out>/*.gbr` | 按 铜箔/板框/丝印/阻焊/锡膏 分组，每层一个文件；走线用 D01 描边、圆弧用 G75+G02/G03、圆形图案用 D03 闪盘 |
| Excellon 钻孔 | `<out>/*_DRILL.drl` | 合并文件 + 按刀单独文件；同时出 `*_DRILL_TABLE.csv` 参数表 |
| NC Route | `<out>/*.rou` | 板框轮廓铣路径（未做刀补，板厂自行补偿） |
| IPC-D-356 | `<out>/*_IPC356.ipc` | 317/327 记录 |
| 贴片坐标 | `<out>/*_PICK_PLACE.csv/.txt` | 位号 / X / Y / 旋转 / 面别 |
| BOM | `<out>/*_BOM.csv` | 按 前缀/类别/面别 分组 |
| 报告 | `--analyze` 直接给：`BRD分析报告_*.xlsx/.txt/.html/.json` + `_csv/` | Excel 多页签、78 列等宽 TXT、自包含 HTML |
| 11 类 PCB 报告 | `<out>/reports/*_Report.rpt/.csv/.html` | 对应 Allegro 的 Report 菜单 |
| 装配图 | `<out>/smt/assembly_drawing.svg` | 毫米坐标，Y 轴向上，四周 2mm 边距 |
| DXF R12 | `<out>/mcad/*.dxf` | AC1009，图层按 板框/布线区/丝印/装配 分开 |
| 设计数据 | `<out>/database/*.csv/.json/.tcf` | 网表、参数、约束、符号表、版本差异 |

**Gerber 有一条安全底线**：如果板框标定不可信（`.brd` 里没写 `Draw Size`，坐标只能靠经验系数换算），`mfg.artwork` 会**拒绝生成**并说明原因——因为用错比例的 Gerber 尺寸一定是错的，交出去比不交更危险。DXF 在这种情况下仍会生成，但会带上警告。

### 12.5 图形界面（界面三）

主窗口底部 **「BRD 分析与输出」** → 打开对话框：

- 左侧填 **BRD / 输出目录 / 可选导出文件**（可以多选一起加进来）
- 右上按 8 大类列出 65 项能力，**双击一行 = 勾选/取消勾选**；不勾选任何一项 = 全部运行
- 点「开始分析」，结果逐项回填状态与结论；点一行可在下方看到**关键指标 / 缺什么 / 怎么补 / 生成了哪些文件**
- 还有 **「只选不符合/数据不足」** 按钮：跑完一轮后，一键筛出有问题的项重跑
- 「导出报告」写出 xlsx/txt/html/json/csv

### 12.6 数据不足时，怎么办

报告里有一个专门的 **「缺失数据与补法」** 页签，把每项缺的数据和在 Allegro 里的菜单路径列成清单。常见的几条：

| 缺什么 | 在 Allegro 里怎么导出 |
|---|---|
| DRC 违规明细（短路/开路/未连接） | `Tools > Design Rules Check` 跑完 DRC，`Tools > Reports` 导出 DRC Report |
| 器件 XY / 旋转角 / 面别 | `File > Export > Placement` 得 `place_txt.txt`；或用本工具的 `--export-skill` 生成 SKILL 脚本 |
| 每个孔的坐标 | `Manufacture > NC > NC Drill` 得 Excellon，或本工具的 SKILL 孔导出 |
| 网络连接关系（引脚级） | `Tools > Reports > Netlist`，或第三方网表 `pstxnet.dat` |
| 测试点 | 让测试厂给 IPC-D-356（`.ipc`），或 `File > Export > IPC356` |
| 叠层介质厚度与 Dk | `Setup > Cross-section` 里抄，或导出叠层报告（这是阻抗/延迟/电容电感估算的前提） |
| IPC-2581 / ODB++ / STEP / IDF / IDX | 必须用 Allegro 对应导出模块生成；本工具只能给「导出准备度清单」 |

把这些文件用 `--analyze-files` 一起喂进来，同一个能力会从「数据不足」变成真实结论——例如 `drc.short`：

```powershell
# 没有 DRC 报告 → 数据不足, 并给出上面的导出配方
python main.py --analyze .\board.brd --analyze-only drc.short

# 补上 DRC 报告 → 变成真实结论 (有/没有短路, 各几条)
python main.py --analyze .\board.brd --analyze-files .\drc.rpt --analyze-only drc.short
```

> 注意：引擎会**自动搜索** `.brd` 同目录下的导出文件。所以只要把 `drc.rpt`、`place_txt.txt` 和板子放一起，直接跑 `--analyze board.brd` 就能拿到完整结论。

### 12.7 没有真实板子怎么验证

仓库里没有 `.brd` 样例，所以附带一套**合成样例生成器**（按 Allegro 17.x 的真实文件结构拼出来，能被本工具原样解析）：

```powershell
python tools\make_demo_brd.py --out demo      # 生成 4 层板 + 配套导出文件
python tools\selftest_analysis.py             # 端到端自检 (合成板 -> 分析 -> 报告 -> 逐项校验)
python tools\selftest_net_locate.py           # 网络定位 (最小 .brd 归档 + CSV)
python tools\selftest_component_stats.py      # 器件分类统计 (全部前缀 + 无数据兜底 + Excel)
```

`selftest_analysis.py` 不只跑一遍看有没有崩，而是按 12.3 的三条纪律逐项校验，并抽查关键结论（线宽该 PASS、故意做的一对 0.0075mm 净间距该 FAIL、导入 Placement 后该拿到 99 个坐标、U7 的 8 个焊盘该量出 1.27mm 间距和到走线 0.868mm……）。当前全量结果是 **65/65 项能力执行、35/35 项自检通过、78 个产物**。

> 合成样例是**测试夹具**，不是真实板子；它只能证明"有数据时算得对、没数据时说得清"，不能代替真板验证。

### 12.8 想改什么，去哪改

| 想改什么 | 去哪 |
|---|---|
| 能力目录 / 加一项新能力 | `src/brd_analysis/cat_*.py` 末尾的 `CATALOG`，注册表在 `catalog.py` |
| 结论状态、指标、缺数据文案的载体 | `src/brd_analysis/model.py`（`Capability` / `Result`） |
| 数据来源与"怎么补"的菜单路径 | `src/brd_analysis/context.py` 的 `howto_*` |
| 几何计算（间距、线宽、长度、圆盘） | `src/brd_analysis/geom.py` |
| 导出文件解析（DRC 报告/网表/IPC-356/坐标） | `src/brd_analysis/imports.py`（坐标部分复用 `src/pick.py`） |
| 报告版式（xlsx/txt/html/csv） | `src/brd_analysis/writers.py` |
| 界面三 | `src/ui/analyze_dialog.py` |
| 合成样例 | `tools/make_demo_brd.py` |
| 自检清单 | `tools/selftest_analysis.py`（网络/器件自检见 `selftest_net_locate.py` / `selftest_component_stats.py`） |

---

## 附：术语

| 术语 | 含义 |
|---|---|
| 单边孔环 | 焊盘外缘到钻孔边缘的最小宽度，`(pad − drill) / 2` |
| 纵横比 (AR) | 板厚 / 最小孔径，PTH 电镀能力指标 |
| CAF | 离子迁移，孔壁间距过小会引发 |
| 覆盖率 | 15（软板 18）项关键输入中，实际拿到值的比例 |
| 可放行 `releasable` | 无 FAIL 且覆盖率 ≥60%，才允许作为放行依据 |
| 缺数据不当通过 | 本工具原则：拿不到值的检查项显式标注，绝不默认 PASS |
