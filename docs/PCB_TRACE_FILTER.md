# Allegro 走线查找 / 高亮（2026-11-26）

## 当前工作流

导入 BRD → 选择铜层、填写线宽/线距 → **在 PCB Editor 查找并高亮**。
程序使用 `AllegroTraceSession` 自动生成私有启动脚本，通过 `allegro.exe -readonly -s` 打开当前板，执行交互命令 `MPA_TRACE_SEARCH`。
不再要求用户生成插件、导入目录、配置网络/差分对/阻抗/颜色或手工在 Allegro 运行脚本。

代码：`src/adapters/allegro_trace.py`（内嵌原生 SKILL 和结果面板）、`src/ui/impedance_panel.py`（仅三项输入）。
历史 `skill/imp_highlight.il`、`src/adapters/impedance_plugin.py` 保留兼容测试，不再是当前工作台执行路径。

## 测量定义与安全

- 层只从当前已成功导入 BRD 获取，执行时再验证当前原生数据库中的层。
- 线宽取原生走线段实际宽度，mil 等值匹配，固定容差 0.01 mil。
- 间距是同层走线段到**不同网络**其他走线段的最小铜边距离，使用 `axlAirGap`，支持直线/圆弧。
- 留空表示不限制；同时填线宽/线距按 AND 筛选，不解析阻抗目标或 CM 继承规则。
- 不覆盖焊盘、过孔、铜皮、差分配对、耦合长度或完整 DRC，不提供阻抗认证。
- 所有几何测量成功后才改变显示。临时高亮使用 PCB Editor 默认高亮色，不修改数据库布线或保存 BRD。
- 高亮生命周期限定在原生阻塞交互面板中，Close/Clear 恢复显示；异常尝试回滚本命令已经高亮的对象。
- 每层最多 100,000 段、2,000,000 距离候选、30,000 高亮对象，超限明确失败，不把部分查找当完整结果。
- 此功能打开自己的只读 Editor，不接管其他 CAD 窗口；重新查找前关闭上次查找窗口。

## PCB 分析与坐标联动

PCB 分析后台使用 extracta.exe 提取坐标/叠构，不打开 Editor。输出每次隔离，校验单位、对象字段和源 BRD 哈希；输入变化拒绝旧结果。只有显式定位/高亮才启动 Editor。
独立 Gerber 分析不启动 PCB Editor；Gerber 未确认 BRD 原点一致时不跨软件跳转。
没有 extracta/Cadence 时可用已确认的同版外部制造资料检查，原生坐标/铜厚显示未获取，不强制启动图形 Editor。

## 验证记录

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest tools/test_allegro_trace.py tools/test_allegro_bridge.py tools/test_pcb_editor_workflow.py -q
```

SKILL 合同测试使用真实 Cadence 语言解释器但**模拟 axl API**，不是 GUI 高亮证明。
真实数据库探测：`tools/verify_trace_search.py`，结果位于 `output/pcb_editor_trace_20261126_probe2/verification.json`。
本机 Cadence 17.4 探测退出码 2，无原生执行回执；源 BRD 保持不变。不能宣称实际高亮或跨软件缩放已经验收。
