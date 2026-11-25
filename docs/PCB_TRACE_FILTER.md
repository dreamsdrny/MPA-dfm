# IMP_HIGHLIGHT — Allegro 原生阻抗 / 走线高亮

## 本次重构

删除了上一版独立 Tk 走线窗口、定时器 mailbox 和对应模拟测试。正式主程序现在有
**Allegro 阻抗 / 走线高亮**页签，原生功能由 `skill/imp_highlight.il` 和 `.form` 实现。
不再启动 Free Viewer 并假定它能够执行 Constraint Manager / SKILL。

## 直接在 PCB Editor 使用

1. 使用有 SKILL/Constraint Manager 许可的 Allegro PCB Editor 打开真实 BRD。
2. 解压交付 ZIP，保持 EXE 和 license.json 同目录。双击 `IMP_HIGHLIGHT/prepare_load.cmd`，
   然后在 PCB Editor 的 File > Script 运行同目录 `load_imp.scr`。脚本无需 Python。
   也可在主程序页签选择插件目录，点击“准备加载脚本”；或生成新的插件目录。
3. `IMP_HIGHLIGHT` 打开原生面板，自动读取当前设计铜层、Net、数据库 Differential Pair、调色板及约束。
4. 选择实际铜层，如 `L5_SIG`（没有该层则不能分析），输入线宽 mil；只限制线宽时把间距留空。
5. 输入线宽和间距时，对所有启用条件做 AND 等值比较，容差为输入的 mil 值。
6. 识别方式：
   - `geometry`：目标层内的几何筛选，**不认定为阻抗线**。
   - `impedance`：有唯一、可解析阻抗目标的约束网络，再匹配实际线宽/间距。
   - `pair`：数据库中真实 Differential Pair 两腿，非根据 `_P/_N` 命名猜测。
7. 颜色直接选择当前 Allegro 调色板的编号/色块，不把 Python 四色硬编码成 CAD 颜色。
8. 点击 Highlight；保持原生阻塞交互面板打开观察临时高亮。Clear / Close 恢复本工具修改的显示。
9. Pick Segment 点击命中走线，命令窗口显示 Net、Layer、实际 Width、已测 Spacing、Pair 和阻抗原始约束。

命令：`IMP_HIGHLIGHT`、`IMP_REFRESH`、`IMP_CLEAR`、`IMP_INSPECT`。
手动 load 时还需 `skill imp_setup("D:/your/plugin" "session-token")`，推荐使用 `load_imp.scr`。

## 主程序集成

主窗口顶部“阻抗 / 走线原生高亮”切换到集成页签：

1. 生成原生插件，在目标 Editor 执行 `load_imp.scr`。
2. 导入当前 Allegro 板目录，从真实 `catalog.csv` 获取层、Net、Pair 和 RGB 调色板。
3. 配置层、线宽/间距/识别方式/颜色，生成执行脚本。
4. 结束旧原生命令，在同一 Editor 的 File > Script 运行 `apply_*.scr`。它通过 `IMP_HIGHLIGHT`
   打开保持显示的原生面板，并重新读取当前数据库。板名或插件 token 不一致会拒绝执行。
5. 点击“读取原生高亮结果”，显示最后一次原生执行的 `results.csv`。界面不是即时 RPC，
   不声称点击 Python 按钮即可直接控制任意已打开 Editor。

导出文件包含原生段实际宽度、间距、两端位置、层、Net、Cline 父对象、段类型、Pair、阻抗规则证据和单位。
`catalog.done` / `results.done` 只在成功关闭 CSV 后写入会话 token 与记录数；新扫描开始时使旧完成记录失效。

## Constraint Manager 读取与边界

- `axlCNSEcsetValueGet(nil)` 枚举本机支持的阻抗类 Electrical 条目。
- `axlNetEcsetValueGet` 获取 Net 生效 Electrical 值，`axlCnsNetFlattened` 补充 pin-pair 规则原文。
- `ELECTRICAL_CONSTRAINT_SET` 属性对应的 ECset 值、Net/Xnet/Pair/父约束组属性也保留。
- `PHYSICAL_CONSTRAINT_SET` 候选和 `axlCNSGetPhysical(cset layer 'width_min/'dp_primary_gap)`
  获取目标层 Physical 线宽/差分主间距。不同候选集冲突不伪造唯一有效值。
- 支持标量阻抗和 `ALL:ALL:target:tolerance` 格式；其他 pin-pair 格式保留原文但不自动映射到所有段。
- 多个不同阻抗目标是 CONFLICT，不能按阻抗模式认定；Physical 区域、class-class 的完整优先级仍待补齐。
- Constraint API 不可用时目录显示 READ_ERROR；仍可用几何模式按当前层/实际线宽筛选，但不能认定阻抗线。
- 阻抗是 CM **目标值**，不是堆叠场求解结果或实测阻抗证明。

## 几何、高亮与安全

- 每次执行先过滤目标 ETCH 层，其他层绝不参加距离比较；直线、圆弧均由 `axlAirGap` 测量铜边距离。
- 差分对间距只比较该对另一腿的 Net 段，不会把旁边更近的无关网络当作差分 gap。
- 当前间距是该段到另一腿/异网段的最小距离，不证明全程耦合长度、平行性或差分完整 DRC。
- `axlHighlightObject(segment nil)` 使用临时高亮，`axlDBControl('tempColor index)` 使用当前 CAD 调色板；
  不再调用永久线段高亮。API 失败会报错并尝试撤销已高亮对象，真实支持仍需目标 CAD 验收。
- 不改铜、不设置约束、不保存 BRD；显示临时状态与其他插件可能冲突，不应并行使用其他临时高亮脚本。
- 当前版本用完整设计路径检查换板，目录/结果完成记录校验内容；但不代表识别同一路径的未保存编辑。Pick 时不声称旧间距仍有效，需重新 Highlight 并测量。
- 对无法解析作用域的阻抗覆盖，状态保持 UNRESOLVED，不按目标阻抗筛选；真实约束层级/数据库回调与高亮效果仍需目标版本实机验证。
- 插件目录改变前必须关闭原生面板；高亮/测量/导出失败会尝试回滚显示，并使旧成功标记失效。
- 只支持当前已打开的设计；编辑或换板后重新 Refresh / Highlight，旧段信息不是当前测量。
- 每层最多 100,000 段、2,000,000 候选、30,000 高亮对象，超限停止，不能把部分覆盖写成成功。
- CSV 是原生工程数据，Net 名称按原文保留；若用电子表格打开，按文本导入，不执行外部名称公式。

## 验证状态

`python -X utf8 -m pytest tools/test_impedance_plugin.py -q`

包含原生 SKILL 解释器语法/控制逻辑合同测试（**模拟 axl API**）、真实主程序页签测试、导出 token/数量校验。
旧测试不再作为真实板实现依据。当前本机实际 `allegro.exe -product Allegro_performance -nograph` 试运行
退出码 2，未生成真实 catalogue；因此尚不能宣称真实板的 CM、高亮、拾取已经验收。
交付 EXE 可运行不代表 CAD 插件已获得许可或通过现场验收。
