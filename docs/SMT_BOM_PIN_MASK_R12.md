# R12：BOM主次料、871同器件脚间距与阻焊

构建标识 `20261127-SMT-BOM-PinMask-R12`。只读客户文件，不执行Excel宏，旧R11交付保持不变。

## 新报告学习

- `2024-1-18-RE-S-860-000302_ODB++_Rev_A_DFM_Report.xlsm`：7条专家摘要、4明细页66条位置。增加Mark在罩内识别/Panel Mark到板边口径。摘要为关闭，但明细仍打开，不能把历史关闭状态当当前板已通过。
- `1111.xlsm`：10条专家摘要、7明细页46条位置。新增客户空洞40%要求、6×6改8×8mm镭雕框、Panel不对称Mark。0.35mm排气孔设计只是工艺候选，不证明焊后空洞率合格。
- 两份明细单位mil；保留来源、全部档值和状态。1111板边损伤50mil与传送边200mil是不同风险，不混成同一默认值。截图9.84mil=0.249936mm，仅当前871证据，不设为全部产线默认值。

## 820 BOM故障修复

文件 `s-870-000820_sch_rev_b.xlsx` 工作表名仍为REV_A，页头Revision B；程序不靠sheet名猜修订。旧代码 strict=True 在Q3重复处拒绝。

表头第5行，数据包括无表头L、M列的“主/次”标识：
- Q3等18个位号主料E-195-000154、次料E-195-000091。
- U20/U21/U22主料E-150-000433、次料E-150-000154。
- 读入331个唯一位号，保留21条次料位号记录（2组汇总料源），不会把主副都作为已贴器件，也不会按行序选首条。
- “主/次/副”只代表料源角色，不是PRIMARY/SECONDARY板面，不是DNP；DNP、尺寸、面别未明确仍需复核。
- 支持有表头sourcing_role/主副/主次，以及无表头末尾精确标识；不从描述中的“主/副”字样猜。未标重复、重复主料、只有副料没有主料依旧报错；明确主料可在副料之后。
- 原标识、行、列、次料MPN保留到“BOM主副料”报告；BOM导出亦保留主副料数据。界面异步导入显示唯一位号和次料数量。

## 新增7类检查（共47类补充检查）

1. **同器件SMD Pin to SMD Pin Spacing**：相同位号不同脚也检查，解决原检查只比较不同器件的漏检。真实铜盘重叠直接报告，非重叠距离必须提供 `smd_pin_spacing_mm` 来源。同盘重复不算相邻脚，明确DNP不查；同网不默认忽略，需有来源的shared_copper_confirmed例外。不同器件间距仍由assembly_pad_spacing检查，避免重复生命周期。
2. **SMD阻焊开窗/覆盖/余量**：支持原生逐盘SOLDERMASK轮廓；有同版对齐最终Gerber时优先最终阻焊。NSMD需 `pads[].mask_policy:"open"`，SMD有意覆盖 `"smd"`；未知策略只复核，原生未取得开窗不等同已证明漏开。扩窗门槛 `smd_mask_margin_mm`。
3. **开窗相连/阻焊桥**：实际逐盘开窗相交候选、`smd_mask_bridge_mm` 最小桥宽；默认候选复核，工艺有意共窗须明确shared_mask_confirmed与exception_source。锡膏和阻焊不是一回事，原生PASTEMASK另外保留，不拿其替代阻焊。
4. **Mark被屏蔽罩遮挡**：罩投影内候选；recognition_stage:"before_shield"说明先识别后装罩，缺罩图/窗口/次序不硬断言。
5. **Panel Mark布局**：明确panel_outline、panel_alignment_confirmed、complete.panel_marks、marks[].scope:"panel"、assembly_sides；不把单板Mark数量当Panel数量。分面数量/中心到板边/三点对称候选；不声称机器已能识别。
6. **二维码镭雕框尺寸**：有来源确认矩形框的旋转最小外接矩形短边与laser_frame_min_mm比较；非矩形暂复核，不当扫码等级认证。
7. **X-ray空洞证据**：thermal_void_records中的confirmed/source/method:"xray"/void_ratio，阈值thermal_void_ratio_max均为0..1。只是当前已确认量测记录比较，不从PCB铜、排气Via或截图反推空洞。

## 871真实板复现与几何修复

后台extracta读取 `1201265-S-860-000871_BRD_Rev_A.brd`，不启动图形PCB Editor。源图位置9779.27/142.76mil，对应248.393458/3.626104mm。

真实对象识别：BOTTOM面U1 11/12脚铜盘相交，面积约0.016129mm²、边距0mm；对应逐盘阻焊开窗相连。程序坐标为实际重叠内部/铜边距离代表点，不复制Cadence DRC marker作为算法结果。原生pin/铜盘数据是CAD对象，不证明物料实体VPL模型。

同时修复原生非对称FIG_RECTANGLE缺旋转：读取Cadence文档定义的PIN_ROTATION_ABSOLUTE，围绕绝对图形中心旋转；已经世界坐标的LINE/ARC不重复旋转。补充OBLONG长盘解码；圆盘弦误差≤0.001mm，与既有ARC一致，避免大量Via轮廓膨胀。缺绝对旋转的非对称标准盘不猜。

大板数据分块：16MiB每文件上限不变。自动关联超过时用同目录、哈希绑定的object_files分块（每块≤8MiB，总输入≤64MiB、≤128块、每类≤200000记录）。分块进入分析/报告来源快照，拒绝路径越界、重复内嵌及哈希变化；不截断焊盘来通过安全预算。原生extraction.json改紧凑输出。CAD pin pitch以空间索引替换O(n²)遍历，避免大器件统计耗尽检查预算。

## 使用

用本轮新EXE导入当前BRD，再“导入BOM表”选820表，启用自动关联可执行分析。BOM不再因明确主次料报重复，但CAD/BOM版本、额外位号、DNP未明确等仍显示复核。

871只导入BRD并执行SMT自动关联即可检测真实铜盘重叠和原生开窗相连；距离不重叠的最小间距需在GUI生成的规则模板填写有来源阈值。新阈值全部null，绝不自动套9.84mil、4mm、8mm或40%。结果和报告保留脚号、相邻脚、量测依据及重叠面积。

旧EXE不会加载更新源码，必须启动新交付目录的EXE，保留同目录license.json。任何partial、输入缺失或工艺未确认都不全板放行。

## 验证记录

- 全套源码回归530 passed、2 skipped、17 subtests passed（160.76秒）。打包合成诊断随后增加R12实际算法断言，相关专项46 passed且无跳过；静态高影响检查及编译通过。
- EXE授权正常及到期/缺失/篡改/错误绑定拒绝均通过，实际主界面/SMT/走线页面启动正常，合成同器件重叠、阻焊相连及R11传送边均产生预期问题，47项补充检查及5张合成图片进入报告。
- 820真实EXE GUI通过异步“导入BOM表”入口读取331位号、21条次料，执行实际SMT自动关联、导出79张问题图及BOM主副料页。没有Editor启动或回调异常，源BRD和Excel哈希不变。SMT结论为“不符合所选能力”，CAD缺位号/装配状态未确认提示仍保留，不将导入成功当板卡合格。
- 工作区 `output/r12_report_bom_871/` 保存只读附件学习、后台提取及算法回归；`output/r12_release_verification/` 保存授权/界面验收；`output/r12_real_gui_820/` 保存820实际链路。不将客户原始资料/输出与管理员私钥随EXE交付。
- EXE SHA-256 `375f2a80af6c4ac1ae653a818c591fc4e7b664eab8e3dcefe05cb31cbc3fcaa3`，约38.73MiB；签名授权2026-11-27至2027-01-25。旧R9/R11 EXE哈希未变。
- 首次R12 EXE真实871 GUI完成：U1 11/12铜盘重叠硬失败、逐盘阻焊开窗相连复核，报告79图、47项补充检查，源BRD不变，无Editor启动/回调异常，无预算耗尽；剩余U9多PLACE_BOUND需本体确认。随后改进脚级图片放大/脚号标注，专项18 passed；最终程序使用独立R12_Final目录，不用该首次构建。
- 最后全套532 passed、17 subtests passed、1个失败，失败位于Gerber GUI创建Tk之前的环境初始化（缺entry.tcl）。对应Gerber组与最新脚级图片测试独立复跑15 passed；没有将环境失败伪记为全套无失败。最终EXE自带Tk资源的实际GUI/授权/47项新算法验收通过。
- 最终R12_Final EXE SHA-256：`a50181ccc9267fc580825e06361e1eef4864b86abef0848d738bca082be67167`；EXE本身已包含脚级取景，不是只更新了源码。最新图片算法回归验证大本体不把小焊端缩到不可见。
- 最终R12_Final EXE重新跑820/871实际GUI全部成功：820通过BOM异步导入331唯一位号/21次料，871保留U1 11/12脚间距fail和原生开窗review；均显示47项、各导出79图，源文件未变，无Editor启动/回调错误。相关业务+统一GUI专项最终63 passed。最终交付附构建后FINAL_ACCEPTANCE.md，不改EXE及授权哈希。
