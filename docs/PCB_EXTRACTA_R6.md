# R6：无图形 Editor 的 BRD 坐标/铜厚提取

- `src/adapters/extracta.py`：只读源板副本、Cadence 多视图单次命令、单位/字段/数量校验、独立产物目录、超时仅停止自身进程。
- `src/application/brd_extraction.py`：自动数据丰富请求，保留人工确认输入；自动读取失败清除旧自动铜厚，不拿历史结果替代本次提取。
- `src/application/pcb_workflow.py`：提取 → 带坐标/铜厚的制造分析 → 哈希校验 → 统一结论；异常/缺失数据不判通过。
- `src/ui/dfm_params.py`：分析条件、只读设计数据、待补充资料；器件原点填结果坐标表，不拿原点距离冒充器件外形间距。
- 取 `LAYER_THICKNESS` 的明确单位，逐铜层保存设计厚度，顶/底各自用于阻焊桥档位。多区域叠构、缺失或不一致均待确认，不把 oz 说明、名称或默认 35 μm 当真实成品铜厚。
- BRD 设计铜厚可用于设计筛查，但制造放行需板厂确认，结果保留阻断项。

本机实测：739 板 extracta 返回 0，279 器件、6 铜层；894 GUI 272 器件、6 铜层，外层 45.974 μm、内层 35.052 μm。
已有 Gerber 分支完成报告/预览，输出在 `output/extracta_gui_894/`，界面尺寸稳定，源资料哈希未变，Editor 启动数为零。

实际开窗/线路几何仍来自 Gerber/NC/ODB 后台资料，不从器件坐标凭空推算阻焊桥。复杂全板 DRC 与阻抗求解不在本次范围。

## 打包 EXE 验收

交付目录 `delivery/Evan_MPA_DFM_20261126_Extracta_Copper_R6/`，不覆盖旧包。
授权、实际主窗口、走线三输入页、SMT 页验证通过，证据在 `output/release_20261126_Extracta_Copper_R6/`。
894 板 EXE 实测：

- 已有 Gerber：`output/extracta_exe_894/verification.json`，272 器件、后台提取成功、Editor 启动数 0、源资料不变、界面尺寸稳定，报告生成成功。
- 仅 BRD 原生：`output/extracta_exe_894_native/verification.json`，同样 272 器件/6 铜层；Gerber 与 NC Drill 导出并检查成功，NC Route 无产物、ODB++ 不可用，明确保留缺口。
- 顶/底面按 45.974 μm 选择阻焊桥能力下限 0.1016 mm；内层为 35.052 μm，不参与阻焊检查。
- 两条流程任务完成，DFM 结论仍为不通过，不把执行成功当制造通过。

自动回归：441 passed，1 skipped，17 subtests passed；后台 GUI 工作流另外单独复跑 4 passed。
