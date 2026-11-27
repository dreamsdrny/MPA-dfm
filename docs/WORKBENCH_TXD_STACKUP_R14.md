# R14：统一工作台、TXD 采集表与叠构剖面

构建标识：`20261127-Unified-Workbench-TXD-R14`。本轮改 GUI 信息架构、报告输出方式、SMT 厂资料入口和叠构显示；BRD 仍是设计主资料，ODB++/STP 仍是需核对的补充资料。

## 1. 界面怎么改

顶部动作条：**导入资料 → 整体分析 · PCB + SMT → 导出分析报告…**。「执行 PCB 分析」和「导出 PCB 报告」移到 PCB 页内部，仍可单独使用。

页签顺序：`项目总览` / `PCB 分析 · BRD / Gerber` / `SMT 分析 · 供应商 / 产线` / `Allegro 走线查找 / 高亮`。总览页显示 PCB、SMT、整体结论三张状态卡与资料路径；结论口径为「缺资料不可放行」。

SMT 页不再出现 YAML 字样：供应商下拉（立景 / Lijing、同兴达 / TXD、外部配置）、产线下拉显示业务名称（内部仍用稳定 key）、`仅分析 SMT`、`补充资料 / 模型…` 折叠 BOM/Gerber/ODB++/STP/对象 JSON/装配规则等次要入口。厂名拼错或被截断时不会偷偷退回另一家能力，而是提示未识别。

整体分析在同一后台任务里先跑 PCB，再用本轮 PCB 的 extracta 结果跑 SMT，并给两份结果盖同一个 `overall_run_id`；合并报告拒绝不同轮次或不同 BRD 的结果。PCB 失败 → 不产出 SMT；SMT 失败 → 保留 PCB 并明确「SMT 分析失败 · …」，整体不放行。独立 Gerber（无 BRD）时 SMT 记为「未执行 · 缺 BRD」。单独执行 PCB 会清掉旧 SMT 结果，避免看起来像同一轮。

## 2. 报告格式与文件

`导出分析报告…` 先选内容（综合 / PCB / SMT，按已有结果列出），再选**一种**格式：`.xlsx`、`.html`、`.csv`、`.txt`、`.json`，然后才弹保存文件框。**只写所选的一个文件，不再自动生成 .txt/.json 附属文件**；已存在的目标文件仍拒绝覆盖。Excel/HTML 保留问题图片与叠构剖面，CSV/TXT 是完整文本内容，JSON 是分析快照（含采集表原文单元格）。

## 3. TXD 采集表完整入库

`tools/convert_txd_survey.py` 只读转换 `config/suppliers/smt/SMT设计端条件采集表-TXD20261113.xlsx`（SHA-256 `f6a48ad68242cae8df5da12d26306c160a853d14fc81fcf49a4b92dcd082039c`），生成 `config/suppliers/smt/smt_capability_txd.yaml`：

- 两个工作表（SMT、SMT+COB）各 54 条编号行，共 **108 条 requirements**，逐条保留类别/工序/评估内容/技术参数/厂填值/设备/检测/设计建议/标准依据及 `工作表!单元格` 出处；合并单元格、公式缓存、批注、链接一并存档，不执行宏、不改原文件。
- 产线 `smt` / `smt_cob` 各 23/22 条定量规则，只在 F 列口径明确时建规则；`bga_pitch`（同表 0.25 与 0.4 冲突）、`chip_0402/0603_spacing`（0.2~0.3 未分档）、`component_to_edge`（＞1mm）、`mark_keepout`（＞1.0mm）、`silkscreen_char_height`（＞0.75mm）、`qfp/qfn/connector_pitch`（「通常为」）等保持 `value: null` → 判为「能力未提供」，不拿 E 列解释文字或立景数值补位。
- 工艺条件页按当前产线只显示本表 54 条；切到 SMT+COB 不串页。报告新增「采集表原文」工作表与 CSV 记录。
- 采集表是厂方填写资料，不等于当前量产已确认；未确认项仍待评审，不能据此全板放行。

## 4. 叠构显示

参考 Valor NPI / DFMStream 等工具的做法，叠构不再只有一张文字表：

- 结果区新增 `叠构剖面` 页（宽区绘制），参数区 `叠构核对` 页保留逐层表与「确认 / 覆盖成品铜厚」「恢复 BRD 设计铜厚」。
- 铜层按**已知厚度等比**绘制；缺铜厚层画灰底斜纹并显示「未获取」，不套用外层值；介质/芯板、阻焊、表面处理画等分示意带并标「厚度未提供」，明确不是实测值。
- 层号与层名去重（`L2 L2 GND` → `L2 GND`）；外/内层标记、来源随选中层显示；剖面与表格双向联动（点色带选行、选行高亮色带），铜厚确认按行 iid 定位，不再从显示文字反解层号。
- 右侧按快照给出的 PTH/NPTH 跨度画实线/虚线符号并标「只表示跨度，不表示逐孔位置」；板厚与铜层合计冲突、缺铜厚层直接写在图头。
- 数据模型在 `src/domain/stackup_visual.py`（纯函数、可测），界面绘制 `src/ui/stackup_view.py`，报告图片 `src/adapters/stackup_picture.py`；PCB/综合报告新增 `叠构剖面` 工作表并嵌入同一张图。
- 介质厚度与 Dk 仍未取得，阻抗、盲埋孔逐孔跨度继续标为待补充，剖面示意不参与判定。

## 5. 修改文件

- `src/ui/dfm_dialog.py`：总览页、顶部动作条、整体分析编排与状态、报告入口。
- `src/ui/report_dialog.py`、`src/adapters/workbench_report.py`：内容+格式选择、单一输出文件、HTML/TXT/JSON 写出、TXD 原文与覆盖检查。
- `src/ui/smt_panel.py`、`src/domain/smt_rules.py`：供应商/产线入口、次要资料折叠、按产线过滤工艺条件。
- `tools/convert_txd_survey.py`、`config/suppliers/smt/smt_capability_txd.yaml`：采集表完整存档与审计映射。
- `src/domain/stackup_visual.py`、`src/ui/stackup_view.py`、`src/adapters/stackup_picture.py`、`src/ui/dfm_params.py`、`src/ui/dfm_results.py`、`src/adapters/pcb_report.py`：叠构剖面、联动与报告图片。
- `src/application/overall_analysis.py`：整体分析的 SMT 快照计算与综合结论出口（UI 不直连 domain）。

## 6. 验证

- 采集表：108 条原文逐格回读一致、合并单元格一致、SHA-256 绑定；两产线各 54 条待评审；冲突项判为「能力未提供」。
- 报告：五种格式各自只产出一个文件，无附属文件；已存在目标拒绝覆盖；HTML 含采集表原文；JSON 保留完整 items。
- 整体分析：PCB→SMT 顺序、同一 `overall_run_id`、混轮次合并被拒、独立 Gerber 时 SMT 未执行且总览显示不完整。
- 叠构：等比/缺失/确认色、层名去重、剖面↔表格联动、铜厚确认与恢复、报告图片嵌入。
- 合成数据与非本机 Tk 环境缺文件跳过均如实记录；打包 GUI 仍是合成数据，`native_brd_tested=false`，不代表真实板放行。

## 7. R14 交付

- 目录：`delivery/Evan_MPA_DFM_20261127_Workbench_TXD_Stackup_R14_Final`
- `Evan_MPA_DFM.exe` SHA-256 `4cd754889ddb557d12c9e109e1b1669a676977691d05e021ecc72f46b948c181`（38.81 MiB）；授权 2026-11-27 至 2027-01-25。
- 包内只有 EXE、`license.json`、`release.json`、`assembly_rules.json`、`smt_bom_attributes.csv`，`.md` 数量 0；R13/R12 历史目录未改。
- 独立验收（`output/r14_release_verification/`）：合法授权通过，过期/错 EXE/缺授权/篡改拒绝；真实主窗口标题 `Evan MPA-DFM - PCB / SMT 制造能力分析`；打包诊断确认按钮文字为「整体分析 · PCB + SMT」「导出分析报告…」、TXD 两工作表 108 行入库、所选 HTML 未生成 .xlsx/.txt 附属文件、51 项装配覆盖与 5 张问题图片仍在。
- 全套源码回归 575 passed、1 skipped（本机 Tcl/Tk 资源缺 `ttk.tcl`，单独复跑 11 passed + 目标用例 passed）、17 subtests；静态高影响规则与应用层依赖检查通过。
- 打包诊断使用合成数据；页面切换截图来自同一套源码 GUI，真实板/真实 ODB++/STP 联合验收仍未做。
