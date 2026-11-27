# R13：BRD为主，ODB++制造层与STP机械资料协同

源码标识：`20261127-SMT-ODB-STEP-Coordination-R13`。已重新打包R13交付，R12_Final历史目录原样保留。

## 三种资料是否都检查？

是，但资料职责不同，不用其中一个取代其它全部检查：

|资料|本轮代码能实现|不能由它单独证明|
|---|---|---|
|BRD|保留设计主资料、SHA-256、extracta位号/坐标/引脚/层序/焊盘/原生开窗；不启动PCB Editor|完整物料实体尺寸、产线工艺、全板制造放行|
|ODB++|Job目录/ZIP/TGZ ASCII子集、选择step；与BRD精确位号/面别/明确料号/DNP/封装名称、引脚坐标/网络、FID焊盘轮廓及可得板框核对；通过空间门控后用铜/阻焊/锡膏/丝印制造层执行已有SMT几何检查|所有ODB版本/符号/负片/文字、step-repeat展开、全网跨层开短路、无Panel资料时的拼板认证|
|STP/STEP|有界只读结构、AP schema、PRODUCT、上下文SI长度单位识别；缺内核明确partial。另提供绑定BRD/STP哈希的确认机械模型导出JSON，将经工程确认本体投影/高度接入已有器件间距/高度/板边等检查|直接自动提取任意STP装配实体、装配变换/镜像、三维碰撞、实体焊端尺寸/引脚极性/VPL认证；控制点集合不是本体包围盒|
|BOM|继续核对位号/料号/MPN/DNP/主副料；主次标识保留|封装模型几何、是否每个脚要焊接、全板装配用途|

目前环境未安装OpenCascade/OCP等三维内核。只选STP会明确“已导入结构/机械几何未解算”；不能把该文件高度或点云自动当实物高度。确认模型JSON是可审核的CAD工程导出，不是程序自己提取STP后已经认证。

## 7份新报告只读学习

|报告|专家摘要|明细页|记录|
|---|---:|---:|---:|
|258|6|5|20|
|293|13|12|94|
|315|12|8|122|
|329|11|9|87|
|401|3|2|4|
|498-adapter|8|7|36|
|482|8|4|16|
|合计|61|47|379|

学习产物保存在 `output/r13_odb_step_review/references.json`，输入XLSM只读、不执行宏；未提供7份板的实际ODB++或STP，因此不能独立复现379条位置。

增补4类检查（不选补充文件时共51类装配检查；选择ODB另外2类、选择STP另外1类）：
1. `physical_pin_mask`：已确认实体引脚接触阻焊覆盖区，实体引脚与铜盘检查不同。要physical_leads确认、实际最终mask及no_solder；只知道MPN/铜盘不能自动推导实体引脚。
2. `laser_trace_test_clearance`：明确镭雕框内同面真实走线相交、测试点边缘到框间距；新增source确认的laser_no_trace、test_to_laser_mm。6mm是外部特定客户要求，不默认套用。
3. `mark_to_silkscreen`：Mark铜边到同面丝印距离，mark_to_silk_mm；不与阻焊扩窗/裸铜识别干扰混成一个量测。
4. `panel_corner_support`：Panel四角废板填补/轨道支撑明确工程评审记录；不凭轮廓包围盒猜有填板，也不凭BRD单板资料放行Panel。

既有检查继续使用：329的CAD/VPL脚数、chip阻焊桥、via-in-pad；315的第一脚/极性需实体及明确映射；258/482后跟上限；401/315 Mark相似铜盘/Panel Mark；498热焊盘排气孔/空洞等。资料不足继续保留缺口，报告三档不自动选档。

## 使用入口与空间门控

源码运行SMT页新增：ODB++归档/目录、step、已确认同版同原点、STP模型、确认模型映射JSON、生成映射模板和清除补充资料。

1. 先导入BRD，启用SMT自动关联/BOM或提供哈希绑定的对象JSON。
2. 选ODB++，多个step必须明确选单板/拼板；未选时保持needs_step。Panel step-repeat不展开实例；绝不合并多step同名位号。
3. 未确认版本/原点/旋转/镜像时只核对身份，不使用制造层进行BRD空间判定；没有自动平移/最近点匹配。
4. 确认后要求BRD/ODB完整位号身份无冲突、引脚清单精确对应、铜层数/顺序可用、全部已取得BRD外层焊盘有一致FID对应。原点/引脚差及轮廓Hausdorff差>0.01mm报告核对失败（解析协同公差，不是厂商制造标准）。缺FID、重复或匹配子集不能解锁制造层。
5. 只有唯一、parsed、无未支持警告的外层film可用于检测；缺mask/paste/丝印继续未获取。geometry_complete仍不全板认证。ODB中的制造层能用不等于STP/VPL/产线确认已完成。
6. 手工Gerber与ODB不能同时当最终层；GUI选ODB会不采用PCB页默认Gerber，但手工已选Gerber需清除，避免混两版。BRD原生铜盘仍保留用于位号脚号关联。
7. 补充文件进入哈希快照；文件/Job目录、STP、模型JSON变化后导出拒绝旧结果。更换输入或BRD会清除旧状态/确认。

STP确认机械模型导出例子（必须由CAD/工程填真实值，示例不是当前板证据）：

```json
{
  "schema_version": 1,
  "unit": "mm",
  "coordinate_system": "brd",
  "alignment_confirmed": true,
  "brd_sha256": "当前BRD哈希",
  "step_sha256": "当前STP哈希",
  "components": [{
    "refdes": "U1", "side": "TOP", "step_product": "STP内实际PRODUCT标识",
    "confirmed": true, "source": "CAD工程导出及版本确认依据",
    "outline": [[10,10],[12,10],[12,13],[10,13]], "height_mm": 2.0
  }]
}
```

GUI“模型映射模板”生成当前两个文件的哈希、可读PRODUCT列表，但components为空且alignment_confirmed=false。没有实体来源不填true。STEP转换单位/多上下文不支持时拒绝模型采用。已有确认BOM本体/高度与模型冲突时保留原值并报告，不静默覆盖。映射只给model_body_confirmed，不授予physical_leads或package_dimensions_confirmed，不让本体高度自动认证BGA球径、实体焊端/引脚pitch。

结果增加`ODB_STP协同核对`页、统一检查状态及图层图片快照。BRD始终继续检查；没有支持全板放行的隐式转换。

## 修改文件

- `src/application/smt_supplements.py`：身份/空间门控、确认模型合并和来源快照。
- `src/application/smt_workflow.py`、`src/application/smt_analysis.py`：服务入口、同一轮SMT几何/状态/报告生命周期。
- `src/adapters/odb_reader.py`：明确每CMP的DNP属性，消除直接导入时的循环依赖。
- `src/adapters/step_reader.py`：有界结构/上下文单位，缺内核保持partial。
- `src/domain/smt_report_extensions.py`、`smt_manufacturing.py`：4类报告增量。
- `src/ui/smt_panel.py`、`src/adapters/smt_report.py`、`issue_pictures.py`：输入入口/状态失效、协同报告/ODB图片。
- `src/application/reports.py`、`templates/assembly_rules.json`：确认模型模板、nullable新规则。

验证涵盖真实格式合成ODB Job/FID→SMT阻焊缺陷/报告、STP单位/截断/哈希、确认本体高度检测、身份/原点/网络/焊盘/step冲突阻止空间采用、输入变化拒绝报告。合成合同不冒充7块真实板复现。

验证结果：全套560 passed、1 skipped（Tk环境缺ttk.tcl，独立复测不属产品缺陷）、17 subtests passed（177.80秒）；新协同/规则/图片专项30 passed，交付CLI及Markdown清理测试4 passed。UI入口独立验证通过，静态高影响规则及编译通过，diff检查无错误（仅已有LF/CRLF提示）。首次UI回归暴露SMT服务兼容入口未透传新参数，已修复并增加服务入口测试，没有将其当环境问题掩盖。

现有真实871哈希绑定BRD对象回归仍显示51项检查，U1 11/12铜盘重叠fail及原生开窗相连review保留；记录 `output/r13_odb_step_review/871_brd_regression.json`。7个附件哈希未变，R12_Final EXE哈希未变。本轮尚无原始ODB/STP实板联合验收。

## R13交付包

- 目录：`delivery/Evan_MPA_DFM_20261127_SMT_ODB_STEP_R13_Final`
- EXE SHA-256：`a73316cd0179381618b3b2ce3c223d5c82399e9cf17e4487c79b66a295dffb72`（38.76 MiB）
- 授权：2026-11-27 至 2027-01-25（60天）
- 交付内容只有 `Evan_MPA_DFM.exe`、`license.json`、`release.json`、`assembly_rules.json`、`smt_bom_attributes.csv`。本轮起**不再随包复制任何 .md 文档**；构建时还会清理输出目录里夹带的 Markdown，历史交付目录不删不改。
- 独立验收（`output/r13_release_verification/`）：合法授权通过，过期/错EXE/缺授权/篡改均拒绝；GUI进入 `Evan MPA-DFM - PCB / SMT 制造能力分析`；构建号R13；SMT打包运行51项装配覆盖，仓位图与BOM入口正常。
- 打包SMT验收使用合成数据，`native_brd_tested=false`，不代表7块真实板或真实ODB/STP已验收。
