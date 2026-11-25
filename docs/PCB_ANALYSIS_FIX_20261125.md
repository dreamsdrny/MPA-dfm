# BRD 导入后 PCB 分析报错修复（2026-11-25）

## 流程与修改边界

工作台 `src/ui/dfm_dialog.py` 捕获输入后调用 `src/application/pcb_analysis.py`；
`pcb_workflow.py` 负责版本校验和分析分支：

- BRD 原生导出：`manufacturing_workflow.py` → Cadence Gerber / NC Drill / NC Route / 可选 ODB++ → 几何检查。
- 同版已有 Gerber：`pcb_workflow._analyze_external()` → `src/adapters/gerber_analysis.py` → 几何检查。
- 两条路径最终都生成问题预览、统一结论；工作台主线程显示结果，报告单独导出。

本次仅增量修改两个实现文件，并新增 `tools/test_gerber_degenerate_arcs.py`。
保留源文件和能力规则版本校验，不放宽 BRD 导入、不返回仅参数“成功”、不改已有交付 EXE。

## 已复现的两个异常

1. **圆弧离散除零**：894 板 `SILK_TOP.art` 的第 3725 个对象（索引 3724），
   圆弧线宽 `0.09144 mm` 恰好等于直径，内侧轮廓半径为零。
   旧代码调用 Gerbonara `approximate_arcs()` 后在 `l/r` 处抛出 `ZeroDivisionError`。
   零线宽描画、零直径孔径也能触发同类问题。
   改为项目内有边界校验的圆弧离散：零半径同端点退化为点，零面积图元保持空面积，
   非零半径同端点保留完整圆；Outline 中心线不会因为零线宽而丢失。
   非法零半径不同端点、非有限数值及超大离散量明确报缺口，不猜测为有效图形。
2. **预览类型不兼容**：真实底片布尔运算后可能得到 `GeometryCollection`。
   Shapely 的此类型不接受 `fill_color`，旧代码生成预览时抛出 `TypeError`。
   改用各几何类型均支持的颜色位置参数，保留面、线、点混合图形。

原生流程预读底片时，单个底片失败记录为 `native_gerber_parse / incomplete`，
不阻断其他有效底片及独立 NC/ODB 检查；失败仍进入覆盖缺口和放行阻断项。

## 894 板实测

只读输入：`D:\Evan\EQ-DFM\26\S-860-000894\S-860-000894_FAB_BRD_Rev_E_LP.brd`，
同名 Gerber 目录及 `定位清单/allegro_bridge`。BRD 识别为 6 层。

- 已有 Gerber 分支：分析、问题 HTML 和 Excel 报告生成成功。
- 本机 Cadence 17.4 原生分支：Gerber 16 个文件、NC Drill 2 个文件导出并分析成功；
  NC Route 无产物，ODB++ Inside 未安装，均如实保留缺口。
- Tk 工作台实测：导入当前板、执行已有 Gerber 分析、渲染 121 行结果成功；
  无界面回调异常，PCB 报告和问题预览按钮启用。
- 输入文件/目录前后内容哈希一致，未改写客户资料。

结果目录：`output/pcb_894_arc_fix/`。
其中 `external/` 保存已有 Gerber 报告及验证；
`native/manufacturing-bnkshgot/` 保存本次原生导出、报告、预览、manifest 和分析快照；
`ui_verification.json` 保存工作台验证，`original_error_reproduction.json` 保存原错误图元。

两条路径的 DFM 结论仍为“不通过（存在不符合项）”，不是执行失败或制造放行。
现有资料还包括板框闭合/悬挂线、内层铜厚未提供、已有钻孔文件缺坐标格式等缺口。
不以名字/默认值补造钻孔格式、内层铜厚或 ODB 对象资料。

自动回归记录见同目录 `regression.xml`；测试覆盖零面积图元、实际问题圆弧、整圆、
顺逆时针、板框中心线、混合类型预览及坏底片记录。真实板分析不代表完整 CAD DRC 或全工艺验收。
