# R9：894 外部 DFM 报告对照、增量实现与交付

版本：`20261127-DFM-Checklist-Assembly-R9`。

## 对照附件

只读 `894_odbjob(1)-V1.0_2_DFM_Report.xlsm`，不执行宏。
附件有 17 类专家汇总，Issue1..24 有 24 类规则/243 条位置；两套清单不是同一统计口径，不相加。
报告 199 个装配件和 BRD 272 对象不同，需要同版 BOM、DNP、文档/机械对象及 VPL 筛选依据，不能强行修改器件数以对齐。
三个规则值不说明具体选档；“Board width”34.3mm 对 100/100/110 的文字描述与数值方向也需核对，因此未硬编码阈值。

## 功能梳理

| 附件检查/问题 | 本轮处理 | 数据边界 |
|---|---|---|
| BOM位号/CAD、封装/料号关系 | 保留精确BOM核对，补入 BRD COMPONENT料号；同封装多料号为复核，不把通用封装正常复用判失败 | 文档/机械位号及装配版本人工核对 |
| Mark相似焊盘、到板边、阻焊干扰 | 原到板边检查保留；新增相似面积铜盘候选、开窗边距算法 | 明确Mark用途及规则，面积相似不代替视觉识别 |
| 器件Pitch | 新增CAD引脚原点最小中心距统计 | 不冒充VPL焊端/数据手册pitch |
| 器件间距 | 保留同面本体/PLACE_BOUND算法和BOM类别关联 | PLACE_BOUND非本体认证，未确认类别不填默认 |
| 丝印无位号 | 后台提取REF DES/SILKSCREEN文字与精确位号关联 | 无文字不自动推断不需标识，保留复核 |
| 双面器件到另一面盘 | 新增波峰焊适用条件下的距离检查 | 必须明确工艺适用性/阈值及真实本体 |
| SMD直接互连 | 同网络走线相交多个同器件SMD盘候选 | 不把同网络连接判电气短路 |
| Hole in SMD | 新增孔跨度/真实孔径与SMD盘相交；review/forbidden/filled_capped_only策略 | 已确认填孔盖铜可作为例外；未知处理不硬失败 |
| 测试盘尺寸、距边 | 原算法保留，开窗核对后确认可接触直径 | 模板/BOM未知用途不猜 |
| 测试点在器件/泡棉下 | 新增同面本体/确认泡棉轮廓重叠 | 有覆盖不自动断言实际探针路线不可达，作为工艺复核 |
| 测试点到丝印 | 新增同面实际轮廓距离 | 阈值必须明确来源 |
| 焊膏非SMD | 新增焊膏超出已确认SMD铜盘候选 | 有意钢网扩展/Mark开窗需复核 |
| SP to SP | 新增独立焊膏开窗间距 | 连通开窗、特殊钢网工艺仍需专项复核 |
| No Pad For Pin | 新增SMD引脚原点落铜候选；支持确认实体引脚轮廓无铜 | 没有VPL不能复现实体焊端缺盘证明 |
| Toe Distance Max | 支持实体引脚/铜盘沿确认前趾方向的延伸量 | physical_leads、toe_direction、toe_max_mm必须明确 |
| CAD/VPL pin count | 新增 expected_pin_count 对完整CAD引脚数核对 | expected_pin_count从已确认模型/BOM输入，不凭封装名猜 |
| Feature to Profile | 保留PCB全层铜边检查与部分覆盖状态 | 异形板框、切槽、规则与原点需核对 |
| Board width | 新增传送X/Y方向和独立最小/最大板宽 | 区分单板/拼板；没有设备限制不判失败 |
| Conveyed Edge Gaps | 内凹边段沿传送方向投影跨度候选 | 不是完整轨道承靠/传感器模型，保持复核 |
| 镭雕区域白油、流板标识、定位孔防呆 | 新增带来源的工程评审记录及缺口显示 | 不凭矩形/孔数量自动认证用途与防呆 |

本轮没有实现完整商业VPL库、完整波峰焊治具模型、镭雕油墨厚度/扫码等级自动检测。上述缺数据明确记录而非显示通过。

## 使用

1. 导入BRD → PCB分析（后台extracta，不打开Editor）→ SMT页导入同版BOM。
2. 启用自动关联；优先本次PCB原生Gerber，外部Gerber需确认同版同原点。
3. SMT执行后查看“装配补充检查/缺口”“资料关联/分面完整性”“对象问题”。
4. 缺规则时点击“生成装配补充规则模板”，填写实际板厂/产线确认数据及source，再导入JSON执行。
5. 需VPL/真实本体/泡棉等时选择工程师确认对象JSON。新增字段：
   - `components[].smd`、`expected_pin_count`；实体引脚 `physical_leads:[{pin,confirmed,outline,toe_direction,no_solder}]`（绝对mm，`no_solder:false`表示明确需要焊接）。
   - `native_pins`、`native_holes`、`native_traces` 自动获取；每类complete标识不能靠手工设true替代证据。
   - `foam_regions:[{id,side,confirmed,outline}]`；实际泡棉覆盖轮廓。
   - `engineering_review.laser_area/flow_direction/tooling_poka_yoke:{status,source,note}`；来源必填。
6. 导出SMT/综合DFM，Issue summary保留问题图片、坐标、来源、候选/硬性判定区别。

随包模板 `assembly_rules.json` 全部阈值为null，不含附件推测值；`smt_bom_attributes.csv`新增smd与expected_pin_count。

## 修正与安全

- BOM核对/装配硬失败纳入综合DFM结论，不能被PCB通过或数值覆盖率抵消。
- 占位本体/未知钻孔工艺/未填规则/不完整对象仍为复核，不放松完整几何门槛。
- Rule JSON有前后/报告导出前SHA-256；source不能为空，有效范围及有限数值校验。
- 候选/图片预算超限明确未完成，只停止本次扫描，不删除原输入。
- 新交付独立目录，保留旧EXE与授权，不替换运行中的用户文件。
