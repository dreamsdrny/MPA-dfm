# Evan Gerber 工作流、报告选项、语言与主题

## BRD → Gerber → 问题坐标

1. 选择真实 BRD，等待离线读取完成。
2. 默认勾选“执行分析时后台导出 Gerber”。点击“执行 PCB 分析”。
3. 程序调用 Cadence **artwork.exe**（不打开 PCB Editor 窗口），使用 BRD 内的 Artwork film 设置输出 Gerber。
4. 导出后解析 RS-274X/X2、圆弧、区域、孔径宏、正负绘制极性，计算制造问题位置。
5. 看“问题 X/Y 坐标”，或点击“Gerber 问题预览”在浏览器查看真实图形和红色问题点。

Cadence 安装与许可仍是 BRD 原生导出的必要条件。板内需已有有效 Artwork films，格式应为 RS-274X/X2；程序不会猜测缺失的制版层配置。
输出目录为 `<BRD目录>/Gerber_DFM/films-<本次标识>`，避免新旧图层混用。源 BRD 不修改。
`source.json` 记录源板/修改时间/导出器，`artwork_export.log` 记录 Cadence 警告。

也可点击“导出 Gerber”单独生成；选“选择已有 Gerber”则关闭自动导出，直接分析目录内同一版本的 Gerber。

### 当前支持的制造检查

| 项目 | 实测依据 |
|---|---|
| 丝印线宽 | 圆孔径描画宽度，并检查最终图形没有把局部细线覆盖成宽区域 |
| 丝印到阻焊开窗 | 同面最终丝印与阻焊开窗几何距离/重叠 |
| 丝印到板边 | 外形层描画中心线到丝印图形距离 |
| 阻焊桥 | 分离开窗之间的最近距离，按当前铜厚档能力 |
| 铜箔到板边 | 最终铜图形与外形中心线的距离 |
| 铜层局部描画宽度 | 低于能力的局部描画候选，标 WARN 需复核 |

Gerber 图形以 0.002mm 近似误差还原；不在该误差内硬判边界。
负片图幅裁切、未识别命令、缺层、多份同角色图层等明确记录为未完整检查；缺层不算通过。
当前未提供完整所有铜箔线距、连通性、区域颈宽、阻焊自身狭缝拓扑、钻孔工艺识别的 CAM 全套检查。

图层识别支持常用后缀（GTO/GBO/GTS/GBS/GTL/GBL/GKO）、X2 FileFunction 和 Cadence SILK_TOP/SMASK_TOP/TOP/BOTTOM 等名字。
例如 847 板原生导出有 14 个 film，其中 8 个是当前制造检查相关层；该板 panel 外形为空，结果会提示无法检查板边。

Gerber 坐标是制版坐标。只有确认 Cadence 导出且零偏移/无镜像/旋转的文件才提供 BRD 定位命令；其他来源在 Gerber 预览中定位，不能擅自映射到 BRD。

## 报告设置

- **报告隐藏低优先级**：隐藏 PCB 低严重度/INFO 明细，以及 SMT low 明细；保持原始结论、覆盖率和错误计数，避免筛选后看起来能放行。
- **需要独立 PCB 报告**：默认关闭，PCB 报告按钮禁用。勾选后再点击导出才写文件。
- PCB 分析不会自动生成 PCB 报告。DFM 综合报告仍可包括 PCB 分析内容，独立 PCB 文件是可选产物。
- XLSX/CSV 格式选项保留；报告记录过滤说明、坐标来源及 Gerber 缺失/解析项。

## English / 中文，Dark / Light

顶部语言和主题下拉框可即时切换，不清除输入和结果。偏好保存在用户 APPDATA。
中英文覆盖工作台控件、参数名称、表头和状态；技术字段保留固定 ID，来源路径/厂商配置自定义文字保留原内容。
深色主题覆盖原生 Tk/ttk 卡片、输入、结果表、文本区；浅色可即时恢复。

Windows 图标为 **EVAN 横排**，9 个原生尺寸；本次新 AppUserModelID 用于新任务栏身份，旧固定快捷方式重新固定新 EXE。

## 验证

- 847 BRD 后台 Artwork 成功导出 14 个文件（含 Cadence 警告，日志已保留）。
- 完整 GUI 流程后台分析、Gerber 预览、过滤报告成功；全程无需打开 PCB Editor。
- English 按钮显示 `Run PCB analysis`，Dark 背景为 #111827，恢复中文浅色验证通过。
- pytest 66 项测试，新增 Gerber 区域/孔径宏/英寸换算/清除极性/报告过滤/语言主题/可选报告测试。

新增模块：`src/gerber_export.py` 后台导出，`src/gerber_analysis.py` 解析/测量/预览，`src/ui/i18n.py` 语言。
新增依赖 `gerbonara`、`shapely`（及 numpy）；打包按 PyInstaller hooks 收集依赖。
