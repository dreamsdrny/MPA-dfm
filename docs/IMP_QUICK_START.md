# 开始使用

1. 将完整 ZIP 解压到有写权限的目录，双击 `Evan_MPA_DFM.exe`。不要只拷贝 EXE，必须保留 `license.json`。
2. 在有许可的 Allegro PCB Editor 打开目标 `.brd`；Free Viewer 不支持本插件的完整功能。
3. 双击包内 `IMP_HIGHLIGHT/prepare_load.cmd`。它会按当前解压位置修复 `load_imp.scr`。
4. Allegro：**File → Script → load_imp.scr**，打开 `IMP_HIGHLIGHT` 原生面板。
5. 选目标层（例如 `L5_SIG`），线宽填 `5`、间距留空即可只筛选 5 mil；填写间距则同时筛选线宽与间距。
6. 识别模式选 Geometry（纯几何）、Impedance（阻抗约束网络）或 Pair（真实数据库差分对）；颜色选 Allegro 实际调色板色块。
7. 点击 Highlight，保持原生面板打开查看；Pick Segment 查看属性，Close 清除显示。不自动保存 BRD。

主程序顶部“阻抗 / 走线原生高亮”进入集成页签，可以准备插件、导入板目录、生成当前参数的执行脚本、读取原生结果。
执行脚本仍需在同一 PCB Editor 中通过 File → Script 运行，不是自动控制其他程序窗口。

**验证边界：**EXE 启动、授权、插件打包资源及控制逻辑已测试；本机真实 Allegro 探测未成功，因此真实板的 Constraint Manager / 高亮 / 拾取仍需现场验收。
若命令失败，请回传 PCB Editor 命令日志及版本，不要把 READ_ERROR、0 命中或未完成视为全板通过。
完整算法边界见 `PCB_TRACE_FILTER.md`。
