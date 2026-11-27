# 架构、代码逻辑与开发说明

> **2026-11-25 分层架构已接入并迁移实际规则/读写：**最新目录、启动合同和迁移边界见
> [LAYERED_ARCHITECTURE.md](LAYERED_ARCHITECTURE.md)。以下内容保留既有算法与交付说明；
> PCB 放行规则现位于 `src/domain/verdict.py`，`src/verdict.py` 保留兼容导出。

更新：2026-11-23。使用入口见项目根 `TUTORIAL.md`；验收与构建见 `ACCEPTANCE_TESTS.md`。

## 1. 本次调整的原则

不是重写全部算法或改成另一个 GUI 框架。本次基于既有改动保留功能与接口，把混在界面里的 PCB 工作流抽出为应用服务，统一入口的输入版本和能力版本门槛；完善盲孔误报防护、60日授权构建和交付验证。

```text
mpa_dfm.py / evan_dfm.py
  ├─ startup_guard：授权 + 固定公钥 + 签名配置校验
  └─ GUI：输入、后台任务、结果展示、报告选择
       └─ services.pcb_analysis：PCBAnalysisRequest 快照 / run_pcb_analysis
            ├─ brd_input：当前BRD导入版本校验
            ├─ 原生 manufacturing_pipeline：导出器、产物哈希、合并覆盖
            └─ 外部 Gerber / ODB++：几何、对象、坐标来源
                 ├─ dfm_engine / dfm_coverage：已确认参数 vs 供应商能力
                 ├─ layout_* / pcb_* / odb_dfm：实际几何与对象检查
                 ├─ pcb_locations / pick：可靠坐标关联
                 └─ verdict：唯一权威结论
                      └─ workbench_report / dfm_report：快照输出
```

## 2. 文件职责和依赖边界

| 层 | 文件/目录 | 职责 |
|---|---|---|
| 入口 | `mpa_dfm.py`、`evan_dfm.py` | 主工作台与授权启动；无控制台日志；管理员授权诊断 |
| 兼容入口 | `main.py`、`gui.py`、`evan_brd.py` | CLI、Checklist/规则初筛与旧窗口，不等价于主PCB几何工作流 |
| 应用服务 | `src/services/pcb_analysis.py` | 数据快照、资料分支、共用前后版本检查；不依赖Tk |
| 输入边界 | `src/brd_input.py` | BRD路径/大小/mtime与解析有效性校验；旧ui导入路径仅重导出 |
| BRD解析 | `brd_design.py`、`brd_raw.py`、`brd_components.py` | XML约束、QuickView、图纸文本和对象摘要，保留来源置信度 |
| 多格式 | `src/design_inputs/` | BRD/BOM/Placement/Gerber/ODB适配、归档预算、资料协同 |
| 原生导出 | `manufacturing_export.py`、`manufacturing_pipeline.py` | 隔离副本导出Gerber/NC/ODB，校验哈希、阶段和可用性 |
| 参数判定 | `dfm_inputs.py`、`dfm_engine.py`、`dfm_coverage.py`、`dfm_stackup.py` | 映射、铜厚、参数能力、未接入能力项缺口 |
| 几何引擎 | `gerber_analysis.py`、`layout_geometry.py`、`layout_drill.py`、`layout_checks.py` | 底片极性几何、空间索引、孔/槽、铜平衡与预算 |
| 实际对象 | `pcb_geometry_checks.py`、`pcb_object_checks.py`、`odb_dfm.py` | 焊盘/孔/阻焊/锡膏/网络锚点；不冒充完整网表DRC |
| 孔类型 | `pcb_hole_types.py` | 结构与工艺分离，冲突/未知不猜；拦截旧名称推算能力输入 |
| 坐标 | `pick.py`、`pcb_locations.py`、`allegro_*`、`silk_position.py` | 实际导出读取、单位、来源、BRD跳转门禁与候选 |
| SMT | `smt_engine.py`、`smt_geometry.py` | 设计/过程能力分离、确认对象轮廓和量测 |
| 统一结论 | `verdict.py` | fail > review > pass；缺数据不放行，所有输出共用依据 |
| 报告 | `dfm_report.py`、`workbench_report.py`、`excel_layout.py` | XLSX/CSV/TXT/JSON、引用、排版与输入字符串安全 |
| GUI | `src/ui/` | Tk主线程控件、TaskRunner后台、过期结果拒绝、用户偏好 |
| 配置 | `config/internal/`、`config/suppliers/` | 内部设计建议与供应商能力分离，不可混称认证能力 |
| 授权 | `licensing.py`、`integrity.py`、`startup_guard.py` | Ed25519、SHA/CRC、AES/scrypt、固定公钥与EXE哈希 |
| 发布管理 | `tools/build_release.py`、`release_support.py`、`renew_release_license.py`、`verify_release.py` | 管理端构建/密钥/续期/实际打包验收，不收进产品运行代码 |

领域引擎不能导入 `src.ui`。GUI 不在 worker 读取Tk变量，先捕获并深拷贝。业务判定不由报告层“修正”，展示过滤也不改变结论。

## 3. PCB 服务调用合同

```python
from src.brd_input import brd_signature
from src.brd_design import extract_brd_design, to_design_params
from src.services.pcb_analysis import PCBAnalysisRequest, run_pcb_analysis

path = r"D:\PCB\board.brd"
signature = brd_signature(path)  # 解析前捕获，防止解析期间换板
info = extract_brd_design(path)
if not info.get("ok"):
    raise ValueError(info.get("error"))
design = to_design_params(info, copper_um=35)
design["_brd_signature"] = signature
request = PCBAnalysisRequest(path, design, factory="hongban", design_unit=info.get("units", ""))
result = run_pcb_analysis(request, progress=print)
```

流程：

1. 深拷贝设计字典，检查导入BRD签名；记录当前供应商能力哈希。
2. 校验补充JSON的BRD哈希与资料合同。
3. 原生模式执行完整制造编排；外部模式先确保资料可用，再执行参数与几何/对象。
4. 保留Gerber/NC/ODB各来源的全部覆盖、问题和错误，后执行来源不覆盖前来源缺口。
5. 所有分支（包括外部ODB）统一检查最终BRD版本和能力哈希；失败丢弃结果。
6. 设置来源、时间和 `analysis_mode`，统一调用 `finalize_verdict`。
7. GUI再检查输入revision；旧worker结果不能变成当前结果。

`PCBAnalysisRequest` 是冻结数据类，但内部dict仍可变；GUI提交前与服务执行时都深拷贝，调用方并发修改仍应避免。`_brd_signature` 使用路径/大小/纳秒mtime，是导入版本门槛，不是密码学版本证明；原生导出/几何补充/报告另有SHA-256校验。保留这些职责差异。

## 4. 规则和数据的可靠性

- 用户单位：QuickView/明确声明用于BRD定位；约束XML按线宽锚点推断的单位只用于约束，不能覆盖坐标单位。
- `VIA20D10` 类命名约定的mil只作尺寸提示，不能证明真实尺寸、实际放置、跨层或用途。
- 已验证Cadence类型/实际跨度可判断结构；机械/激光工艺要另行确认，证据冲突返回unknown。
- 旧来源含“焊盘栈命名/按孔径/推算/启发”的孔能力值，在覆盖与报告补充规则执行前移除；保留info说明与放行阻塞。无来源的手工输入仍按用户确认值处理，操作者须选择正确字段。
- 参数判定读取供应商 `pcb_capability_*.yaml`，记录文件、修订、SHA-256；未知板厂/等级、重复键、非法使用单位不静默回退。
- 内部对象基线违规只列复核；用户显式限值才按对应硬门槛判断，适用性必须确认。
- 顶底阻焊与铜厚逐层检查；不拿外层厚铜能力套全部内层最小规则。

## 5. 几何与对象算法边界

Gerbonara负责底片解析，Shapely/GEOS负责极性布尔与距离，STRtree负责邻域过滤，避免全板N×N矩阵。规则输出测量/候选、源文件、对象、单位、坐标和覆盖状态。

| 规则 | 当前边界 |
|---|---|
| 线宽/窄颈 | 描画和相向边界候选，不是完整骨架最小宽度证明 |
| 铜间距 | 独立铜块距离、连通内部窄槽候选；网络不完整不硬判全网短路 |
| 阻焊桥 | 独立开窗及内部候选，合并开窗不代表逐引脚全覆盖 |
| 孔环 | 实际圆/槽孔壁到铜边界，明确镀覆/工艺/跨度/对齐才执行 |
| 孔重叠 | 轮廓与跨度，未知/重复候选复核；堆叠盲孔不能直接报通孔重叠 |
| NPTH/孔壁网络距 | 明确属性、网络、范围及板厂限值，未对齐不比较 |
| 铜密度/平衡 | 封闭板框含挖空及网格统计；无确认门槛只统计，不保证翘曲/压合 |
| ODB对象 | 原生FID关联外层焊盘/过孔/阻焊/锡膏，未知图元和未绑定身份保持缺口 |
| 丝印 | 实测直线/宽度与文字/圆弧外框候选分离，最近器件不是对象归属 |
| SMT对象 | 已确认body/place_bound边界、类别、面别和完整性，不替代过程量测 |

轮廓弧近似0.002mm，双边距离约0.004mm判定带需CAM复核。局部裁切的合格距离是安全下界，不声称精确全板极值。默认邻域候选/问题/网格预算耗尽要记录未完成。百万稀疏合成点空间索引测试不等同百万真实Gerber图元全规则验收。

ODB支持ASCII子集、gzip/Unix .Z与可解析常用符号；并非完整商业CAD所有符号、负片、step-repeat、文字/条码或全拓扑语义。归档防路径穿越、链接、重复路径和解压超预算，不能因容器盘点完成宣称每项DFM完成。

`geometry_complete=False` 在目前保持；实际执行过不等于覆盖完整。任何未知资料、未接入能力、部分覆盖或候选阻止完整自动放行。

## 6. 开发、构建和授权

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -X utf8 -m pytest tools -q
```

Tk、PyInstaller依赖开发环境；EXE用户无需Python。Gerbonara数据文件、动态BRD类别/GUI/服务模块及新增YAML必须收集，主配置在 `MPA_DFM.spec`。构建禁用UPX。

```powershell
.\.venv\Scripts\python.exe -X utf8 tools/build_release.py `
  --out "delivery\Evan_MPA_DFM_20261123" `
  --admin "C:\Users\SZV01201\AppData\Local\EvanMPADFMReleaseAdmin\20261123" --days 60
```

管理工具生成独立发行者、公钥策略、签名清单，保留原 `config/license_policy.json` 的正式初始化状态，不覆盖正式私钥。私钥只在独立管理目录以Windows DPAPI保护，本Windows账户才能解密；**不进入交付目录/EXE**。需受控备份管理员目录与Windows账户恢复能力，丢失该私钥无法为这个EXE续签；不能仅导出公钥恢复签发能力。

交付程序ID为 `Evan.MPA_DFM`，文件名/GUI为普通产品名，许可证绑定最终EXE的SHA-256。60日计算为 `expires=starts+59天`，到期当天有效，次日拒绝。管理员续期：

```powershell
.\.venv\Scripts\python.exe -X utf8 tools/renew_release_license.py `
  --admin "C:\Users\SZV01201\AppData\Local\EvanMPADFMReleaseAdmin\20261123" `
  --exe "delivery\Evan_MPA_DFM_20261123\Evan_MPA_DFM.exe" `
  --output "output\renewed_license.json" --start 2027-01-22 --days 60
```

验收使用独立副本，不替换交付license，不动原程序/业务资料：

```powershell
.\.venv\Scripts\python.exe -X utf8 tools/verify_release.py `
  --release "delivery\Evan_MPA_DFM_20261123" `
  --admin "C:\Users\SZV01201\AppData\Local\EvanMPADFMReleaseAdmin\20261123" `
  --evidence "output\release_20261123"
```

`--check-deployment <结果JSON>` 是EXE管理员验收出口，仍执行完整启动校验，只输出结果不创建业务窗口，不能绕过授权。验收工具另外启动实际GUI并检查普通标题。

独立 `Evan_License_Manager` 工程继续用于正式发行者管理、AES文件加密及吊销，不修改该工程。其正式私钥与本次独立发行者不同，不能用不同发行者的授权为当前EXE续期。

## 7. 已知待完善事项

- 离线日历日期没有可信在线时间、防回拨和强制远程吊销，不能宣称不可破解DRM。
- 当前完整制造拓扑/最小宽度/焊盘栈/公差/装配可达性未实现；不得删除缺口来宣称放行。
- 外部制造资料到BRD全版本/空间一致性仍须可靠证据。
- Windows 10、企业终端杀毒、不同CAD版本和ODB原生转换器需目标机器验收。
- Tk仍为当前GUI；应用服务独立后可为将来WPF/PySide提供边界，但本次未实现替换前端或RPC。
- 保留兼容入口和原算法文件，不以“大整理”为由删除用户工作或旧业务接口。

## 8. 目录规范与走线筛选模块（2026-11-24）

**2026-11-24 原生插件重构：**下面的 `trace_filter*` / 独立窗口属于已撤销旧方案，不再作为当前入口。
当前为 `skill/imp_highlight.il` + `.form`，`src/impedance_plugin.py` 生成插件目录/执行脚本，
`src/impedance.py` 严格导入带会话记录的原生 catalogue/results，`src/ui/impedance_panel.py`
集成正式工作台第 3 页签。删除旧独立 GUI、timer mailbox 与旧模拟测试，保留非走线功能/用户资料。
发布配置收集 `skill/`；插件命令每次重新读取当前设计，不通过定时器跨回调保留 dbid。
实际命令测试与模拟合同测试分开；完整操作/算法边界以 `PCB_TRACE_FILTER.md` 的当前说明为准。

### 文件存放和依赖方向

| 位置 | 约定 | 走线筛选相关文件 |
|---|---|---|
| 根目录入口 | 只做启动/授权/异常处理，不放领域算法 | `mpa_dfm.py` / `evan_dfm.py`；独立开发入口 `evan_trace_filter.py` |
| `src/ui/` | Tk 控件与事件；主线程读取变量，`TaskRunner` 做后台 I/O；关闭时取消自己的任务 | `trace_filter_dialog.py`，主窗口 `dfm_dialog.py` 和兼容窗口 `main_window.py` 提供入口 |
| `src/` | 与 Tk 无关的领域数据、校验与外部适配；不能依赖 `src.ui` | `trace_filter.py` 筛选合同；`trace_filter_skill.py` 生成 Allegro 工具；`trace_filter_bridge.py` 管理隔离会话与版本校验 |
| `tools/` | 测试、开发和发布管理代码，不放产品秘钥或客户文件 | `test_trace_filter*.py`、`build_release.py`、`verify_release.py` |
| `config/` | 内部设计基线和板厂能力分开；发行公钥/配置由发布脚本签名 | 走线筛选未增加规则配置，不把规则当成实测 |
| `docs/` | 设计合同、用户使用和验收边界 | 本文、`ACCEPTANCE_TESTS.md`、`PCB_TRACE_FILTER.md`；用户入口在 `TUTORIAL.md` |
| `output/` | 报告、构建验证和临时生成物，不作为源码或发布私钥位置 | 示例工具包 `output/trace_filter_skill/`（本机生成，可重新生成） |
| `delivery/<独立版本>/` | 不覆盖旧交付；EXE、匹配 license、文档、发布清单；交付区只保留一个版本文件夹（不生成 ZIP） | 由 `tools/build_release.py` 构建 |
| `%LOCALAPPDATA%/...ReleaseAdmin/` | DPAPI 保护签发私钥，独立于交付包 | 构建过程、私钥；不要复制至交付目录 |
| `store-brd/` / `store-bom/` / 外层 `存档/` | 用户/客户资料与历史存档 | 只读输入，不纳入发行包、不自动清理 |

`MPA_DFM.spec` 是正式主程序构建配置，自动收集 `src.ui` 和 `src.services`；`Evan_BRD.spec` 为忽略跟踪的旧兼容程序配置。新入口由主窗口引用，`trace_filter` 三个非 GUI 模块由 GUI 静态导入，打包验收要实际打开子窗口，不仅检查源码导入。

### 修改规范

1. 保留未知用户改动，不重置、不覆盖旧交付或输出，不批量移动文件。
2. 路径、条件等 Tk 变量先在主线程捕获；后台只接收普通值，完成前核对路径和版本；CSV 使用 SHA-256 检测意外变更。哈希不是带密钥的签名，不能防有意伪造数据。
3. `.brd` 的路径、大小、mtime 是现有版本门槛；原生测量通过 PCB Editor 从数据库取得，不将规则值当作实测。离线导入只接受带 CSV 哈希的版本记录。
4. Allegro 交互由生成的 SKILL 与数据式请求协议完成；用户输入不作为可执行表达式，原生脚本不保存 BRD。改变可写设计时必须重新扫描。
5. `OUT_OF_RANGE` 表示搜索半径内没有候选，不是精确距离；失败、未知 Net 或线宽不表示合格。走线工具是筛选，不合并到主 DFM 的权威结论。
6. 增量代码提供 `tools/test_*`，执行针对性测试、全量回归、Ruff 和实际 EXE 授权/GUI 验收；Cadence 模拟 API 测试不等同真实板联调。
7. 现有签发密钥存放和许可策略保持独立，不用批处理中的强制结束用户进程流程；选择新发行目录、新管理目录构建并验证。

### 运行与文件生成

主程序“PCB 线宽/线距筛选”从主窗口带入当前路径和已配置 Editor，但子窗口仍需自己完成原生实测。每次新建隔离临时目录保存 `.il/.form/.scr`、实测 CSV、请求/应答文件与 `trace_source.json`；不自动删除，以便排错。用户选择目录生成原生工具时，程序不会擅自删除旧数据；若已有同名测量 CSV，要求另选目录。筛选 CSV 由用户明确选择保存路径。

2026-11-24 可靠性修复：逐序号请求/回执防止命令覆盖；清除显示不依赖源文件时间戳，但仍核对 Editor 当前设计；导出完成标记含段数，和 CSV 哈希共同校验。旧 schema 2 仅允许非空 CSV；新 schema 3 带完成段数，确认空板需明确 0。走线扫描候选和图形有上限，超限不能标为完成；GUI 分页、预览采样、后台筛选均保留完整结果和过期检查。

CAD 实测、图层可见性、原生表单、线段高亮/清除、点击拾取与许可证支持尚需在目标 Allegro 版本及真实 BRD 上验收。`axlHighlightObject` 的 `t` 表示永久显示高亮而非传入颜色，线段是否支持及原有高亮如何处理依版本而定；本工具不修改设计走线，也不会自动保存，但不能保证与其他高亮工具的状态完全隔离。
