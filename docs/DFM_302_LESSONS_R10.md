# 302 DFM报告学习与代码增量（R10）

版本 `20261127-DFM-302-Mechanical-R10`。只读附件，不执行宏，不修改原报告。

## 报告口径

附件 `1S-860-000302_ODB++_Rev_A_DFM_Report.xlsm` 有6类专家汇总，Issue1..4共66条位置（12/38/4/12）。
详细表头明确mil；不能因为上次894报告是mm而沿用：120mil=3.048mm，160mil=4.064mm，3mil=0.0762mm，0.39mil=0.009906mm。
原报告4mm与160mil存在细微差别，且三个档值未明确最终选档，未自动固化为产线规则。
新增 `src/adapters/dfm_reference.py` 通用只读报告读取器，逐明细页取表头单位，保留原值/换算值/全部阈值档位，单位缺失或歧义不猜测。未确认规则和BRD原点均保持false。
第一处D3/C307实测95.446mil=2.4243284mm，位置4388.6631/6577.8402mil=111.47204274/167.07714108mm；原点仍未与源BRD验证。
提取记录 `output/external_dfm_302_review/report_extract.json` 同时保留原值/单位和换算值。

## 新增/改进检查

| 问题 | 实现 | 边界 |
|---|---|---|
| D3 PTH与反面SMT距离 | 将PTH实际焊接面铜盘与该面SMT铜盘作边到边距离，不用本体投影代替 | 明确PTH、SMD、面别与阈值；缺资料保持复核 |
| M1..M4螺丝孔净空 | 真实孔壁/明确螺钉头净空区到同面器件本体；PLACE_BOUND仅候选 | 不将M前缀认定螺丝用途，不用钻孔中心到器件原点代替边缘距离 |
| C266 SMD接大铜 | 计算焊盘**外部**halo邻域铜占比；明确两焊端配对可比较差异 | 铜焊盘本身100%落铜是正常现象，不能作为铜连接过大/阻焊覆盖比例。新量测是热风险代理量，非Valor同算法或热仿真 |
| Heel Distance Min | 沿已确认前趾轴的负方向计算后跟铜盘余量，与toe分开 | 要实体引脚轮廓/轴向/匹配铜盘；无需焊接例外或身份未知不硬失败 |
| 2mm支撑肋 | 接受有来源的已确认长度/宽度与最小尺寸比较 | 不猜板框窄处就是支撑肋；length/width不得互换，自动最小骨架未实现 |
| 软硬结合板分板 | 明确板型与Punch/激光等设备/工艺评审、记录缺口 | 不凭层名认定产线有设备，不以单一通用硬板阈值放行 |
| 丝印压SMD盘 | 沿用已有实际同面铜盘/丝印间距检查与Issue summary图片 | 不复制附件0.39mil为当前供应商默认规则 |

所有新增检查进入“装配补充检查/缺口”、对象问题、统一检查状态与报告；失败保留，缺完整清单仍不全板放行。

## 输入与使用

规则JSON扩展键（填写source及确认版本，所有数值单位mm，ratio为0..1）：
`pth_to_opposite_smd_mm`、`mounting_clearance_mm`、`heel_min_mm`、`thermal_halo_mm`、`thermal_copper_ratio_max`、`thermal_imbalance_max`、`panel_rib_min_mm`。
模板全部null，需由当前产线/板厂确认，不能直接把报告选档填入。

BOM新增：`pth`、`mounting_hole`、`mounting_sides`（TOP/BOTTOM或用分号分隔）、`thermal_pair_confirmed`。
通过精确位号关联孔记录；没有关联孔位或真实直径不使用器件原点冒充。PTH/SMD冲突会停止该身份对应检查。

确认对象JSON支持：

- `mounting_holes:[{id,mounting_confirmed,x_mm,y_mm,diameter_mm,assembly_sides}]`；或`keepout_confirmed:true,keepout_outline`明确螺钉头/机械净空区域。
- `components[].pth:true`、`smd:true`（不能同时true）、`thermal_pair_confirmed:true`及`physical_leads:[{pin,confirmed,outline,toe_direction,no_solder}]`。
- `panel_support_ribs:[{id,confirmed,measured_mm,dimension_basis:"length"或"width",source,outline?}]`。
- `board_type:"rigid_flex"`与`board_type_confirmed:true`；`engineering_review.rigid_flex_separation:{status,source,equipment_confirmed,method:"punch"或"laser"或"other_confirmed",note}`。
- `complete.mounting_holes/panel_support_ribs`必须来自确认的完整清单，不凭结果缺陷数为零填写true。

## 验证

新增 `tools/test_smt_mechanical.py` 覆盖焊接面比较、孔壁/本体距离、占位复核、后跟与前趾区分、焊盘外铜占比、支撑肋口径、设备缺数据与ratio输入校验。
本轮没有提供302原始BRD/ODB++/Gerber/BOM，因此不宣称在302源设计上独立复现全部66条缺陷；报告位置仅为对照证据，算法使用合成合同与现有板资料回归。
本轮仅更新源码，不覆盖或重新打包R9交付EXE。

验证记录：最后全套485 passed / 1 skipped / 17 subtests passed；其后热邻域索引优化及两端热差测试专项54 passed，编译和高影响静态规则通过。
首次全套出现7个Tk初始化环境错误（找不到scrlbar.tcl），对应统一GUI组独立复跑8 passed；未将环境错误记录篡改成成功。
现有894 BRD/BOM资料回归读取276个BOM位号，显示24项装配检查；新增6项在缺明确机械用途/本体引脚/规则/设备确认时均为未获取/需复核，没有自动采用302报告门槛。记录 `output/r10_894_regression/result.json`。
