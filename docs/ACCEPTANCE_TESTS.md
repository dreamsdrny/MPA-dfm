# 测试、交付和维护说明

更新：2026-11-23。使用见 `TUTORIAL.md`，实现边界见 `ARCHITECTURE.md`。

## 1. 本次代码复查与调整

- 基于现有未提交改动增量整理，不回退原功能。
- 从GUI抽出 `src/services/pcb_analysis.py`；原生/外部Gerber/外部ODB都执行同一前后BRD和规则版本门槛。
- `src/brd_input.py` 负责非GUI导入校验，旧 `src/ui/brd_input.py` 保留兼容重导出。
- GUI只捕获输入/提交后台服务/按revision接受结果；命令行原生制造工具共用服务。
- 孔型和工艺分开；旧名称/孔径推算值不能重新进入机械/激光能力与补充报告规则。
- 机械孔环定位排除盲孔、未知孔与未确认机械工艺；“待确认”不显示伪机械能力阈值。
- 新限期发布工具生成60个日历日签名许可并绑定最终EXE哈希，私钥独立管理，主界面无用途标签。

## 2. 自动化验证

整理前全量基线：**249 passed / 1 skipped / 17 subtests passed**。本轮全量：**264 passed / 2 skipped / 17 subtests passed**；两项跳过是本机Tk初始化偶发失败，随后单独补测 **2 passed**。高影响Ruff规则及Python编译通过。

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest tools -q --junitxml=output/release_20261123/pytest.xml
.\.venv\Scripts\python.exe -X utf8 -m ruff check src tools evan_dfm.py --select E9,F63,F7,F82
.\.venv\Scripts\python.exe -X utf8 -m compileall -q src tools evan_dfm.py mpa_dfm.py
```

关键回归覆盖：

| 测试 | 内容 |
|---|---|
| `test_annular_ring_false_positive.py` | VIA8-16无跨度、旧推算值、报告未复活误报、真实机械输入仍判失败 |
| `test_cadence_hole_types.py` | Cadence结构、证据冲突、名称提示、旧IS_THROUGH不能冒充孔型 |
| `test_pcb_analysis_service.py` | 快照不被worker改写、原生/外部规则与BRD变更拒绝、缺制造资料不返回参数结果 |
| `test_workbench_ui.py` | 实际Tk后台响应、禁用门槛、输入变化、过期结果、报告状态 |
| `test_release_license.py` | 精确60日首尾、次日拒绝、Windows DPAPI私钥往返/篡改 |
| `test_deployment_cli.py` | 诊断出口仍调用授权校验，不绕过许可证 |
| 既有制造/几何/ODB/SMT/报告测试 | 未知属性、缺层、预算、对象错配、单位、报告、单一结论 |

两项补测为逐层铜厚参数面板、未导入BRD禁用分析与报告；记录见 `output/release_20261123/ui_retest.xml`。全量记录仍保留原skipped，不改写成全量零跳过。

## 3. 900板误报复验

只读输入：

- `D:\Evan\EQ-DFM\26\S-860-000900\S-860-000900_FAB_BRD_Rev_A.brd`
- 同目录 `定位清单/allegro_bridge/evan_holes.csv`
- 原 `900-PCB报告.xlsx` 未覆盖。

原报告把VIA8-16名称推算孔环0.1016mm与机械孔0.1524mm下限比较，并计314个位置。取消孔径/名称猜测后，机械孔环输入为None；实际离线证据不足时保持待确认。重新分析、生成并读回工作簿未再出现该机械孔环误报，不据此判定盲孔工艺通过或全板放行。

重构后再使用应用服务执行同版现有Gerber及坐标分析：机械孔环误报 **0**；最终仍为“不通过（存在不符合项）”，不隐藏其他制造问题。源BRD和原制造文件前后SHA-256一致。底片复制到项目输出目录后生成预览/报告，不写回客户资料目录。
复验报告：`output/release_20261123/900_recheck/900_重新检查_PCB报告.xlsx`，同目录 `verification.json` 保存检查结果。

## 4. 交付目录与授权

本轮交付目标：`delivery/Evan_MPA_DFM_20261123/` 及同名ZIP。
包内只含最终EXE、有效license、三份文档及哈希/日期清单；不含发行私钥、过期/篡改授权样例、旧EXE或客户业务资料。

60日授权有效范围：2026-11-23～2027-01-21（含首尾）；2027-01-22起拒绝启动，需有效续期。日期以交付 `release.json` 和签名license为准。GUI/窗口标题与EXE使用普通名称，不显示“测试版”。没有license也不能启动。

管理员目录在交付之外，例如 `%LOCALAPPDATA%/EvanMPADFMReleaseAdmin/20261123`。DPAPI保护签名私钥，需当前Windows账户解密；保护并备份管理员目录及账户恢复能力。续期方法见架构说明。源码正式发行策略不改为自动免授权。

打包验证工具在独立临时副本检查：有效、过期、缺失、签名篡改、错误EXE哈希。另启动实际GUI、核对普通窗口标题并正常关闭；验收结果输出 `output/release_20261123/verification.json`。这些验证不替换交付包的有效许可。

**本次EXE实测结果：**

| 场景 | 结果 |
|---|---|
| 有效签名与配置 | 诊断退出0，授权起止日期符合60日合同 |
| 已到期许可证 | 退出1，明确“授权已到期” |
| 缺失许可证 | 退出1，拒绝启动 |
| 修改到期日但未重签 | 退出1，签名无效 |
| 许可证绑定另一EXE哈希 | 退出1，目标程序校验失败 |
| 实际GUI启动/关闭 | 创建 `Evan MPA-DFM - PCB / SMT 制造能力分析`，无“测试版”标签，正常关闭退出0 |

程序为PyInstaller 6.22.3、Python 3.12.13、Windows 11 x64构建，UPX关闭。测试诊断仍是完整授权门槛，不修改系统日期。实际GUI验收隔离偏好目录，不加载/改写用户上一次的BRD选择。

## 5. 文档清理范围

主项目仅保留：

1. 根目录 `TUTORIAL.md`：操作、资料、定位、授权和故障处理。
2. `docs/ARCHITECTURE.md`：层次、逻辑、合同、开发和管理员维护。
3. `docs/ACCEPTANCE_TESTS.md`：验证、交付、安全及未验证项。

旧的分散教程、历史更新日志/审查文本和过时截图在内容整合后删除；其文件名与哈希清单保存于 `output/release_20261123/document_cleanup.json`，不是另一份用户教程。

不清理外层“存档”、旧delivery交付内容、业务输出/测试数据、LICENSE或独立授权工具工程。旧delivery文档是历史快照，不作为本次使用入口。

## 6. 不能宣称已验收的内容

- 无完整全网络跨层DRC、完整骨架最小宽度或全部制造/装配工艺覆盖；部分几何结果不自动放行。
- 无全CAD版本兼容保证，ODB++ Inside本机原生转换需实际安装/许可验收；合法测试产物链路不等于原生CAD转换实测。
- Windows 10/多机DPI/企业终端防护需目标机器矩阵；当前机器冒烟不能代替全部部署验证。
- 离线授权依赖本机日期，未接入可信时间服务器，不保证阻止系统回拨/反编译或远程强制吊销。
- EXE使用配置/许可签名，不等同Windows Authenticode代码签名；企业发布建议另行代码签名。
- 杀毒是否误报须实际产品扫描；本机Defender返回 `AMServiceEnabled=False`、`AntivirusEnabled=False`，未执行扫描，不能写“杀毒通过”。

## 7. 2026-11-24 走线筛选增量与重新构建

**当前重构取代下述旧方案：**改为原生 `IMP_HIGHLIGHT` 交互命令、当前层过滤、
Electrical/Physical 原文证据、真实数据库 Pair 及临时高亮；正式工作台集成页签。
旧 `trace_filter*` 模块/独立 GUI/测试删除，当前测试为 `tools/test_impedance_plugin.py`。
语言引擎合同测试的 axl API 是显式模拟，不能代表真实许可/约束优先级/高亮验收。
实际 `allegro.exe` 原生探测退出 2，未产生 catalogue；目前未完成真实 PCB Editor 验收。
本轮重构交付目录为 `delivery/Evan_MPA_DFM_20261124_IMP/`，其中 `IMP_HIGHLIGHT/` 提供原生文件。

- 正式主入口 `mpa_dfm.py` 使用的 PCB/SMT 工作台已增加走线筛选按钮；旧 Checklist 窗口仍保留兼容入口。
- `trace_filter.py` / `trace_filter_bridge.py` / `trace_filter_skill.py` 分别负责筛选、隔离会话、生成原生脚本；GUI 位于 `src/ui/`，测试位于 `tools/`。
- 自动测试含条件、单位、圆弧模拟、错误/未知处理、不同 BRD 和 CSV 哈希变更、GUI 后台过期结果、主窗口入口。Cadence `cnskill` 测试使用模拟 axl 数据库，仅验证 SKILL 运行逻辑。
- 新发布包沿用签名/授权流程并在新的独立目录创建，不覆盖旧包；发布验证除既有 5 类授权和主 GUI 外，新增 EXE 内主窗口打开走线子窗口的实际测试。
- 实际 Allegro 曾停在 Product Choices，未完成真实 BRD 的线段 API、高亮和拾取验收；真实数据、未保存编辑、CAD 版本及许可差异仍须客户现场确认。不要把 EXE GUI 冒烟或模拟测试写成真实 CAD 测量验收。
- 本次准确测试数量、EXE 哈希及验证结果以新交付包的 `release.json` 和 `output/release_20261124_TraceFilter/verification.json` 为准，旧版本上面的记录不被替换。
