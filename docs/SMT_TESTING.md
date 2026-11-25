# SMT 立景能力对比与检测（2026-11-24）

## 测试入口

1. 解压整个交付包，保持 `Evan_MPA_DFM.exe` 和 `license.json` 同目录。
2. 导入当前 `.brd` 并等待读取成功，打开 **SMT 厂 · 工艺能力分析 / YAML**。
3. 本页自动加载 `smt_capability_lijing.yaml` / `automotive`，无需再次找文件。
   “内置立景能力”可恢复默认；“导入 SMT YAML”仍支持其他供应商。
4. 输入已确认参数，空白项引用 PCB 页签已确认的同名字段，未知数据保持空白。
   拼板 X/Y 从 PCB 页签共享；立景按长边 ≤300 mm、短边 ≤250 mm 判断，允许旋转。
5. 点击“执行 SMT 分析”。“供应商能力检测”查看状态、能力、裕量和来源。
6. 默认同时检查公司内部 SMT 基线，在“内部基线对比 / 检测”中分别查看：
   - 同一输入是否满足内部基线；
   - 供应商允许范围能否覆盖内部基线全部允许范围。
   两者不是同一个结论。例如 BGA 距板边 2 mm 可满足立景 ≥1 mm，但不满足内部 ≥5 mm。
7. “工艺条件（人工评审）”保留原能力表的 **54 条**条件，选择条目可查看设备、检测方法、设计建议与参考。
8. 导出 SMT 报告。Excel/JSON 保留供应商检测、内部检测、内部基线对比、对象问题与工艺评审条目。

## 自动检测的资料边界

**只导入 YAML 或 BRD 并不代表已完成全板 SMT 几何检测。**

- 没有对象 JSON 时，执行的是已确认参数与定量阈值比较；界面/报告明确写明未执行对象几何检测。
- 对象检测要求真实器件本体/占位轮廓及面别。贴装原点、丝印或封装名称不能代替本体边界。
- 内部和供应商 YAML 原始门槛保持不变；无能力承诺的项目不补造阈值。
- 不符合优先于待复核；部分资料不能产生完整通过。量化项全匹配时仍提示工艺条件待评审。
- 54 条原表文字不是 54 个独立自动算法；附加设备/制程/治具/稳定量产条件仍需人工评审。
- 回流炉温、烘板/表面处理限制、局部 Mark、01005 稳定量产、防呆和应力建议不会靠文字自动判通过。
- 当前 SMT 页签不会自动从 BRD/ODB++ 获取完整器件本体、高度或拼板切线。

## 对象 JSON：先生成模板，再确认真实数据

点击“生成对象资料模板”，程序写入当前 BRD SHA-256，但所有坐标对齐/完整性标志保持 `false`。
填写实际资料后设 `coordinate_alignment_confirmed: true`；确认哪个对象清单完整，再将对应 `complete` 设 `true`。
所有坐标/对象尺寸为 **mm、当前 BRD 原点与轴向**，不自动平移/旋转/镜像。
相对文件路径相对于 JSON 所在目录。JSON 的 BRD 哈希、关联文件哈希或输入版本不一致则停止分析。

关键对象格式（仅格式示例，**不能作为您的板实测值**）：

```json
{
  "components": [{
    "refdes": "U1", "side": "TOP", "category": "bga", "boundary": "body",
    "outline": [[10,10],[12,10],[12,12],[10,12]],
    "tall": true, "package_dimensions_confirmed": true,
    "pitch_mm": 0.4, "ball_diameter_mm": 0.2,
    "body_length_mm": 2, "body_width_mm": 2, "height_mm": 8
  }],
  "marks": [{"id":"FM1","side":"TOP","global_confirmed":true,"x_mm":5,"y_mm":5,"diameter_mm":1}],
  "assembly_sides": ["TOP", "BOTTOM"],
  "tooling_holes": [{"id":"H1","tooling_confirmed":true,"x_mm":5,"y_mm":5,"diameter_mm":3.1,"plated":false}],
  "vcut_lines": [{"confirmed":true,"points":[[0,4],[20,4]]}],
  "test_pads": [{"refdes":"TP1","confirmed":true,"side":"TOP","x_mm":6,"y_mm":6,
    "diameter_mm":1,"outline":[[5.5,5.5],[6.5,5.5],[6.5,6.5],[5.5,6.5]]}],
  "shields": [{"confirmed":true,"side":"TOP","outline":[[10,10],[15,10],[15,15],[10,15]]}]
}
```

上例只是需要嵌入完整模板的对象字段，不是可直接导入的完整文件。
`board_outline` 是实际板框闭合多边形，不是随意包围盒。
`outline` 是绝对坐标多边形，已有旋转后的实际轮廓；程序不重复旋转。

支持器件类别：`chip_01005 / chip_0201 / chip_0402 / chip_0603 / chip_0805 / tantalum / qfp / qfn / bga / connector / crystal / transformer / sensor / other`。
`chip_0805` 表示按 0805 及以上间距组执行，须由工程师确认分组；未知类别保持空白。
`package_dimensions_confirmed` 表示尺寸已从器件资料确认，不以 PCB 焊盘球径替代器件球径。
测试点到器件距离按 `height_mm` 分 <5、5~8、≥8 mm 三档，并仅比较同面对象。
测试点到板边/器件/屏蔽罩用实际边缘，测试点 pitch 用中心距；罩内测试点距覆盖区为零。

### 已支持的对象检查

- 同面器件本体间距；混排取适用项中最严格门槛；BGA 0402/0603 禁布及高件避让。
- 器件/分封装到板边、V-CUT 最短距离。
- 经确认的 BGA/QFP/QFN/连接器 pitch、BGA 球径与外形尺寸。
- 全局 Mark **按贴装面分别统计**、共线复核、直径/到边/无铜区。
- 定位孔数量、直径、非金属化及到板边距离。
- 测试点直径、同面中心距、到板边、按器件高度分档间距及屏蔽罩避让。
- 配合同版本/已核对 Gerber 与对象资料，检查丝印线宽、字高及焊盘/阻焊开窗距离。

### 文件关联

可选 `components_file`（Placement TXT / SKILL CSV）、`component_bounds_file`（带位号的 PLACE_BOUND CSV）、`bom_file`；
每个文件必须同时填写 `<字段名>_sha256`。`components` 可作为位号匹配的类别/尺寸/轮廓补充，不能猜最近位号。
`boundary: place_bound` 仍会提示是否等同本体边界待复核。
Mark 无铜区要求 `other_copper` 及对应 `complete.other_copper_top/bottom`，丝印要求同版本 Gerber 的 `gerber_sha256` 和已确认对齐。

## 建议验收案例

1. 拼板输入 250×300、板厚 1.6 mm：供应商尺寸项应通过；不要求手动交换 X/Y。
2. 拼板接近设备上限、板厚 1.0 mm：应提示供应商复核，不直接通过。
3. BGA 距板边 2 mm：供应商通过、内部基线不符合。
4. 完整清单中 TOP 两个 Mark、BOTTOM 一个 Mark：不能合计为三个通过。
5. 相邻 TOP 0402 / 0805 本体间距 0.35 mm：混排按 0.40 mm，判不符合；反面器件不混算。
6. 测试点/封装/V-CUT 未提供或未确认完整：应显示数据不足/待复核，不冒充无适用对象。
7. 修改 BRD 后沿用旧对象 JSON：应拒绝几何判定。

## 已有自动化验证

源码回归覆盖能力阈值与单位、对象距离、资料合同、内部/供应商区别、报告读取、UI 结果失效及后台过期结果拒绝。
实际包装验证结果见交付配套 `verification.json`、`smt_ui.json` 与 `smt_packaged.xlsx`（合成数据，不是用户板检测结论）。
实际生产板完整性、Cadence 同步定位、设备/治具与企业杀毒兼容仍需您实机验证。
