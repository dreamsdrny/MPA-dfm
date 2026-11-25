# Gerber 独立分析修改记录

仅修改 `D:\Evan\MPA-DFM\MPA-DFM`，没有修改 `D:\Evan\SCH-AI`。

## 本次新增

- `src/adapters/gerber_input.py`：只读单文件、目录、压缩包导入，安全暂存和图层读取。
- `src/application/gerber_analysis.py`：独立导入/分析用例，输入与能力版本校验，不依赖 BRD。
- `src/application/gerber_diagnostic.py`：打包后合成 Gerber 验收。
- `src/ui/gerber_panel.py`：默认 Gerber 工作台、图层清单、能力/叠构配置、结果及报告。
- `tools/test_gerber_standalone.py`：导入、分析、预览、报告、输入变化、安全归档和真实 Tk 事件循环测试。
- `tools/capture_gerber_ui.py`、`tools/verify_gerber_example.py`：布局截图与隔离复制项目示例验收。
- `docs/GERBER_STANDALONE.md`：使用、资料格式及限制。

## 本次调整

- `src/adapters/gerber_export.py`：扩展 Gerber 后缀和格式头识别。
- `src/adapters/gerber_analysis.py`：X2 Profile/Legend、Cadence ETCH、KiCad 图层角色；修正板名 `_BOARD` 被误当 bottom 的原有问题；无效文件校验。
- `src/application/pcb_workflow.py`：独立 Gerber 报告源校验分支，保留 BRD 原流程。
- `src/application/pcb_inputs.py`：叠构导入返回来源哈希，供 Gerber 版本验证。
- `src/application/reports.py`、`src/adapters/pcb_report.py`：Gerber 报告、SHA-256 来源记录、禁止覆盖输入、移除空 BRD 器件坐标表。
- `src/ui/dfm_dialog.py`：Gerber 默认入口、主操作状态、BRD/CAD 高级折叠区、移除 Gerber 输出按钮。
- `src/ui/dfm_params.py`、`src/ui/dfm_results.py`：高级 BRD 引导与 Gerber 毫米问题坐标显示。
- `src/ui/responsive.py`、`src/ui/i18n.py`：隐藏非当前流程报告按钮、响应式重排与中英文名称。
- `evan_dfm.py`：Gerber 命令行输入、发布诊断入口，不自动加载历史 BRD。
- `tools/build_release.py`：发布包加入 Gerber 使用说明。
- `tools/test_manufacturing_pipeline.py`：读取已有 UTF-8 source.json 时显式指定编码，解决 Windows GBK 测试失败。
- `TUTORIAL.md`、`delivery/README.md`：新版本入口与最终发布路径。

项目中原本已有大量未提交修改；本次未重置或清除它们，未删除任何历史发布或业务文件。

## 验证

- 最终 EXE：`delivery/Evan_MPA_DFM_20261125_Gerber_Final/Evan_MPA_DFM.exe`。
- 最终 EXE Gerber 合成验收：6 图层、98 个问题坐标，实际几何执行，资料不全不放行。
- 项目既有示例底片（隔离副本）：9 图层、120 个问题坐标，原始文件不修改。
- 最终 EXE SMT、原生高亮页签验收通过；不宣称已完成真实 Cadence/生产 BRD 验收。
- 1100×720、1366×850 界面布局检查：主工具栏无溢出，高级区域默认隐藏。
- 详细结果：`delivery/evidence/GERBER_20261125_Final/`；布局截图在 `delivery/evidence/GERBER_20261125_v2/`。
- 新增业务模块和独立 Gerber 测试的 Ruff 检查通过。
- 回归测试结果以最终验收目录的 `regression.txt` 为准。
