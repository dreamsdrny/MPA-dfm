# Evan MPA-DFM 使用教程

更新：2026-11-25。主程序为 **Evan_MPA_DFM.exe**，默认打开 **PCB 分析 · BRD / Gerber** 统一界面。
**新版入口：** 导入 BRD / Gerber 文件、压缩包或整套目录 → 确认共用能力与叠构 → 执行 PCB 分析 → 预览问题/导出报告。
Gerber 无需先导入 BRD；BRD 与 Gerber 共用资料区、参数区、结果区。完整说明见 [GERBER_STANDALONE.md](docs/GERBER_STANDALONE.md)。
新版右上角构建标识为 `20261125-PCB-Unified-ArcFix`；旧 EXE 不会自动读取更新后的源码。
SMT 立景能力自动加载、内部基线对比、对象检测与验收说明见 [SMT_TESTING.md](docs/SMT_TESTING.md)。
源码与架构见 [ARCHITECTURE.md](docs/ARCHITECTURE.md)，验证与构建见 [ACCEPTANCE_TESTS.md](docs/ACCEPTANCE_TESTS.md)。交付包中这两份文件与本教程放在同一目录。

## 1. 安装、启动与授权

Windows 10/11 x64：解压交付 ZIP 到有写权限的普通目录，保持以下文件在一起：

```text
Evan_MPA_DFM.exe
license.json
TUTORIAL.md
ARCHITECTURE.md
ACCEPTANCE_TESTS.md
PCB_TRACE_FILTER.md
release.json
```

双击 EXE；无需安装 Python。也可将 `.brd` 拖到 EXE，或在 PowerShell 执行：

```powershell
.\Evan_MPA_DFM.exe "D:\PCB\board.brd"
```

单文件 EXE 首次启动需解压资源，可能稍慢。原生制造导出、打开 BRD 和同步定位需要本机 Cadence PCB Editor 及有效许可；已有同版本制造文件的离线扫描不需要 CAD 转换器。

交付附带 **60 个日历日签名授权**，按交付日起算，含首尾日期。具体有效日期以各交付包的 `release.json` 和 `license.json` 为准；到期后必须更换有效续期授权。界面保持普通产品名称，不显示版本用途提示。

- 许可证必须与 EXE 位于同一目录；缺失、篡改、过期或程序哈希不匹配时拒绝启动。
- 不要手工编辑日期；需管理员重新签名后替换 `license.json`。
- 配置与发行者公钥在 EXE 内签名校验。重打包后须重新签发匹配的新授权。
- 当前许可按本机日历日期验证；离线模式没有可信网络时间，不能保证抵抗系统时钟回拨。
- 授权不删除程序、原始 BRD 或业务数据，也不通过自毁实现到期。

## 2. 先导入 BRD

1. 选择有效 `.brd`，等待读取完成。
2. 确认板厂（红版/四会）、板型、能力等级和外层成品铜厚，铜厚单位为 **μm**。
3. 检查自动带入参数及其来源，补填已实际确认的数据。
4. 缺失项保持空白；示例值、未知孔型、名称推算尺寸不能冒充实测。

没有成功导入当前 BRD 或 Gerber 时，PCB 分析与报告按钮禁用；SMT 仍要求有效 BRD。文件大小/修改时间发生变化要重新读取；输入变化会清除旧结果。后台分析期间编辑参数，其过期结果会被丢弃。

**BRD 约束不是实际几何。** Constraint Manager 的最小线宽/线距只表示允许规则；快照与图纸文字也不能证明全板实际测量。FAB 拼板 BRD 可能没有 `objects/Part.xml`，读不到器件时需补同版 Placement/对象导出，不能从丝印猜位号。

## 3. PCB 分析的三种资料路径

### A. 从 BRD 一键原生导出（默认）

保持“BRD 一键导出并分析 Gerber / NC Drill / NC Route / ODB++”启用，设置 PCB Editor，点击“执行 PCB 分析”。程序在隔离副本上调用：

| 工具 | 输出与要求 |
|---|---|
| `artwork.exe` | 板内已配置的 Artwork films；未定义图层会列缺口，不自动猜制版层 |
| `nctape.exe` | 实际 NC Drill；校验 METRIC/3.5/绝对坐标/零偏移等原生日志与参数 |
| `ncroute.exe` | 已定义的槽孔/铣孔；没有产物不代表设计没有槽孔 |
| `brd2odb.exe` | ODB++ Inside 转换器，需额外安装与许可；“设置 ODB++ 导出器”可指定路径 |

某阶段缺工具/许可仍分析其他可用制造资料，报告明确记载缺口；所有制造几何都不可用则停止，不返回仅参数“分析完成”。本机若未安装 ODB++ Inside，不能因已有 Cadence 就认为能导出 ODB++。

新版 GUI 原生输出位于程序目录下 `output/PCB_Manufacturing/manufacturing-*`，不再默认写回源板目录；包括原生文件、日志、`manufacturing_manifest.json`、分析 JSON 和 HTML 预览。源 BRD 不保存、不修改。源和产物哈希发生变化时拒绝使用旧结果。

### B. 已有 Gerber / Excellon

选择同版 Gerber 目录会退出一键模式。未要求重新导出时分析指定目录；没有目录仍尝试从当前 BRD 导出。资料不可用时停止，不退回参数-only 成功。

孔检查需要真实 Excellon NC Drill/Route，不是绘有钻孔符号的 Gerber。若未明确孔工艺、镀覆、跨层、坐标格式或对齐情况，相关规则列为未完成。

### C. 已有 ODB++

选择“ODB++ 对象资料”指定同版目录/ZIP/TAR/TGZ。多 step 资料选择实际单板 step；不要混合 panel 与单板。此路径执行对象 DFM，不额外调用 Artwork。

核对铜层数，仍需人工确认外部资料与 BRD 的版本、原点、镜像和旋转。未证明对齐时保留制造坐标与预览，不生成猜测的 Allegro 跳转命令。

## 4. 铜厚、孔型与补充几何资料

### 4.1 逐铜层叠构

点击“导入逐层叠构 JSON”。`stackup` 只列铜层；每层长度单位 mm，铜厚 μm。例如：

```json
{
  "layer_count": 3,
  "copper_um": 35,
  "stackup": [
    {"index": 1, "name": "TOP", "copper_um": 35},
    {"index": 2, "name": "INNER", "copper_um": 18},
    {"index": 3, "name": "BOTTOM", "copper_um": 35}
  ]
}
```

信号、电源和地层均检查；内层缺铜厚不采用外层默认值。阻焊桥和焊盘到防焊只检查首尾铜层。板级最小规则不能作为各层实测。实际门槛以所选供应商 YAML 为准，档位空隙/超范围不外推。

### 4.2 机械孔、盲埋孔、激光孔

**孔型结构与钻孔工艺是两回事：通孔不自动等于机械孔，盲埋孔不自动等于激光孔。**

- Cadence `Thru Pin/Thru Via` 表示通孔，`BBVia/Microvia` 属于非贯通结构；通用 `VIA/PIN` 对象标签不能证明实际用途。
- 焊盘栈名称由用户定义，`VIA8-16` 不证明是通孔；孔径 ≥0.15 mm 也不证明机械通孔。
- `VIA_LIST` 是允许使用清单，不证明该焊盘栈已经放置；同径钻孔表不能映射实际对象用途。
- 名称推算的 `(盘径−孔径)/2` 仅供复核，不作为实测孔环。真实偏心/槽孔使用实际孔壁至铜边界。
- 历史 CSV 的 `IS_THROUGH=YES/NO` 可能来自未验证对象属性，不能替代 Usage/跨层证据。
- 孔型、工艺或尺寸缺失时显示“待确认”，不产生机械孔环误报，也不直接判激光孔通过。

本次 `900.brd` 误报即来自把 `VIA8-16` 推算孔环 0.1016 mm 套入机械孔 0.1524 mm 下限。已在提取、旧输入防护和定位边界分开处理。请在 PCB Editor 的 Padstack/Usage 报告核实实际跨度和工艺。

### 4.3 几何补充 JSON

点击“生成补充资料模板”，填写真实数据后选择“补充几何资料 JSON”。顶层合同：

```json
{
  "schema_version": 1,
  "brd_sha256": "必须替换为当前BRD的实际SHA256",
  "geometry_options": {
    "drill_directory": "同版钻孔目录",
    "drills": {
      "pth.drl": {
        "plated": true,
        "process": "mechanical",
        "start_layer": 1,
        "end_layer": 3,
        "alignment_confirmed": true
      }
    },
    "limits": {"npth_to_copper_mm": null, "pad_to_mask_clearance_mm": null},
    "copper_balance": {"cell_mm": 10, "max_delta_percent": null}
  }
}
```

此例仅说明格式，不代表这块板的真实工艺。未知值保持 null/false，核实后才能确认。

- `process` 是 mechanical/laser；跨层为实际铜层号，不能按孔径或文件名猜。
- 对齐确认表示制造文件单位/原点/旋转/镜像一致，不执行自动对齐，也不证明与 BRD 跳转对齐。
- `nets` 可按 Gerbonara 孔对象零起始索引提供网络；不是工具号或孔径。
- 逐焊盘/过孔轮廓需真实铜盘多边形、网络、层、位号/引脚；不能用包围盒、锡膏或丝印替代。
- `mask_policy=open` 仅用于工艺明确要求整盘开窗；`smd/required/tented` 须确认适用对象。
- 内部对象规则是设计建议；显式覆盖的门槛必须由用户/板厂确认，不代表供应商认证。
- 相对路径依 JSON 所在目录解释；模板中的占位哈希不能直接用。

## 5. 查看问题、真实坐标与 Allegro 定位

先看“制程能力比对”“问题 X/Y”“几何覆盖与缺数据”，再看最终结论：

| 状态 | 含义 |
|---|---|
| 不通过 | 有已判定失败项，其他通过项不能抵消 |
| 需复核 | 无硬失败，但存在缺数据、候选、部分覆盖或未满足门槛，不能放行 |
| 通过 | 全部适用的放行条件满足；当前完整制造几何算法仍未覆盖，不能借局部扫描宣称全板放行 |

蓝色“完成”只说明任务完成，不等于制造通过。风险评分、已检查项数量也不等于制造良率证明。

### 坐标导出

“打开 BRD”在专用 Allegro 窗口建立连接并提取对象。自动发现失败时用“设置 PCB Editor”。输出一般为 `<BRD目录>/定位清单/allegro_bridge`。也可点“生成 Allegro 坐标脚本”，在同版 Editor 中执行：

```text
skill load("D:/PCB/exports/evan_pick.il")
skill evan_pick_all("D:/PCB/exports")
```

完整脚本 `evan_coordinates.scr` 还导出丝印/板框；可通过 Allegro File > Script 执行。核对日志及导出数量，产品/许可选择未结束时会等待。

| 导出文件 | 数据用途 |
|---|---|
| `evan_components.csv` / `COMP_XY.txt` | 器件实际放置原点、面别、旋转 |
| `evan_holes.csv` | 孔中心、实际读取的钻孔直径、焊盘栈和网络；孔用途仍需确认 |
| `evan_pads.csv` | 引脚外框，不等于阻焊开窗或完整铜盘 |
| `evan_traces.csv` | 线宽、端点、层、网络 |
| `evan_silkscreen.csv` / `evan_outline.csv` | 丝印对象和板框 |
| `evan_component_bounds.csv` | 能关联同位号的 PLACE_BOUND 线段，闭合后仍须确认边界语义 |

选择坐标来源目录后重新分析。`COMP_XY` 支持 `VERSION/UUNITS/!` 文本，BOTTOM 镜像标志保留，不额外平移或镜像坐标。

报告保留 `X/Y(mm)`、`X/Y(BRD)`、BRD 单位和来源。例如 BRD 单位为 mil 时：

```text
x -100.500000 200.250000
```

负坐标及数据库原点保留。未知 BRD 单位不生成猜测命令；若 Editor 已改为 mm，使用 mm 列。双击有已验证 BRD 坐标的记录可联动定位；“LOCATED”回执才表示 Editor 已响应。制造坐标未证明对齐时用底片预览定位。

### PCB 走线线宽/线距筛选

主窗口点击 **阻抗 / 走线原生高亮**，进入集成页签生成 `IMP_HIGHLIGHT` 插件。在有许可的 Allegro PCB Editor 打开真实 BRD，通过 File > Script 加载 `load_imp.scr`。原生面板读取当前铜层、Net、真实 Differential Pair 和 Electrical/Physical 约束；选目标层（如 `L5_SIG`），输入实际线宽或线宽+间距，选择当前 Allegro 调色板色块后执行。可只做几何筛选，也可先按阻抗约束网络/数据库差分对识别，不能仅凭线宽判阻抗线。

只分析所选 ETCH 层；差分间距测量真实对内另一腿，不含铜皮、焊盘或过孔，不代替 DRC/阻抗场求解。使用临时线段高亮，保持原生命令面板打开，Close 清除显示，不自动保存。主程序通过导出的当前板目录选择层/颜色，可生成同一 Editor 的执行脚本再读回结果，不是假装即刻远程控制。真实 CAD 许可/版本和显示仍须现场验收。详细操作见 `PCB_TRACE_FILTER.md`。

丝印文字/圆弧外框和最近器件关联属于候选，不是实际字形/归属证明。机械孔环定位只使用已确认通孔结构、机械工艺及实际尺寸；无可靠资料不编造314个违规孔。

## 6. SMT 分析

1. 先成功导入当前 BRD。
2. SMT 页默认加载内置立景 YAML 与 `automotive` 产线；也可导入其他供应商 YAML。默认同时检查内部 SMT 基线。
3. `scope: design` 使用确认的设计值；`scope: process` 需要产线实际量测，不能用设计尺寸替代。
4. 需要对象检查时选择同版对象资料 JSON，再执行 SMT 分析。

对象 JSON 必须声明 `schema_version:1`、当前 `brd_sha256`、`coordinate_system:"brd"`、`unit:"mm"` 和已核实的 `coordinate_alignment_confirmed:true`。外部 BOM/坐标/轮廓/Gerber 的 SHA-256 必须匹配。对象轮廓是已转换到 BRD 原点、方向的真实多边形，不再按旋转角二次旋转。

封装类别、body/place_bound 边界、Mark 的 global_confirmed、定位孔 tooling_confirmed/plated、盖油或开窗策略均须确认。`complete` 仅在清单确已完整时设 true。缺失对象、不完整清单和未知门槛不判通过。对象规则不代替 SMT 过程量测。

## 7. 制造资料与 BOM 协同

点击“Gerber/ODB++ ↔ BOM”，导入制造文件及 BOM，可在此入口独立核对资料，不要求 BRD；它不是 PCB/SMT 主分析的替代或完整放行。

- BOM 支持 XLSX/XLSM/CSV 等可识别表格；老 `.xls` 先另存 XLSX，OrCAD DSN 先导出 BOM。
- 普通 Gerber 缺位号时须提供确认的器件 CSV，不能从铜焊盘平均值伪造器件原点。
- ODB++ 多 step 必须选实际 step；器件原点/面别/旋转保留原约定，不擅自转换贴片机角度。
- 配对后双向核对位号、料号、MPN、值、封装、DNP，保留双方原记录，不自动决定哪边正确。
- 未核对位号、重复位号、数量差和版本不明都列缺口，不因文件配对成功就判制造通过。
- 可导出 XLSX/JSON 和叠图，文件变化需重新导入。

源码兼容入口还支持 `store-brd/store-bom` 资料库配对、Checklist 模板填写和八类 BRD 审查。它们保留原有接口，不把初筛当完整 CAM 检查。

## 8. 报告导出

启用“输出 PCB 报告”后，在报告栏选择 XLSX/CSV，点击 PCB、SMT 或 DFM 综合报告。

| 报告 | 前置条件 | 内容 |
|---|---|---|
| PCB | 当前 PCB 分析完成 | XLSX + TXT，或 UTF-8 BOM CSV |
| SMT | 当前 SMT 分析完成 | XLSX + JSON，或 CSV |
| DFM 综合 | PCB/SMT 都完成且来自同一BRD | XLSX + TXT + JSON，或 CSV |

PCB XLSX 包含 Main、Issue summary、Info、IssueN、逐项能力、问题坐标、器件坐标、来源和几何覆盖。综合报告 PCB/SMT 页签有对应前缀。CSV 单表按“记录类型/范围”区分内容，多位置逐行输出。

默认文件名 `<BRD文件名>_PCB报告.xlsx` 等，保存前可改目录/名称。隐藏低优先级只影响展示，不改变结论、计数和缺数据依据。预览调用相同报告导出器，真实 Excel 打印需安装 Microsoft Excel。

## 9. 常见问题与边界

| 情况 | 处理 |
|---|---|
| 授权/完整性失败 | 检查同目录 license、日期和对应EXE；联系管理员，不改签名文件 |
| 缺 Artwork/ODB 导出器 | 设置正确工具并确认许可/板内film；可选同版外部制造资料 |
| 只读到规则但无实际图形 | 查看制造导出日志与覆盖缺口，不用规则数值冒充实际检查 |
| 盲孔报成机械孔 | 重新导入并分析；核对 Usage/跨层/工艺，不以孔径或名称改类型 |
| 坐标跳转不准 | 核对设计单位、原点、镜像、版本；制造资料未对齐不可跳转BRD |
| FAB 图无器件 | 补同版 Placement/COMP_XY/对象导出或使用设计源板 |
| 补充JSON哈希不匹配 | 当前版本重生成模板，拒绝套用旧资料 |
| 扫描超预算/未知图元 | 标未完成；缩小明确输入或补支持，不能当无违规通过 |

当前提供部分制造几何与对象检查，尚无全网络跨层开短路证明、完整最小线宽骨架、全部焊盘栈/非功能盘、完整位置公差或全装配/治具可达性模型。`geometry_executed=True` 与 `geometry_complete=False` 可以同时存在。未支持、缺数据、复核候选和预算耗尽都阻止完整自动放行。

程序只读源 BRD/BOM/制造资料；报表和原生导出写到独立输出。崩溃日志通常位于 EXE 旁 `Evan_DFM_crash.log`；回传日志、报告及版本来源以便复查，不必发送私钥。
# Gerber 独立分析（2026-11-25）

新版默认进入 Gerber 工作台，无需先导入 BRD。导入文件/压缩包或整套目录，确认板厂、铜厚和叠构后执行分析。
主界面不再显示多余的 Gerber 输出按钮；BRD/CAD 操作收进高级区域。
使用说明和限制见 [Gerber 独立分析](docs/GERBER_STANDALONE.md)。历史 BRD 流程仍在高级页签保留。
