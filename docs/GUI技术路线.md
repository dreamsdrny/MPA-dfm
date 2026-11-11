# MPA-DFM 界面响应与后续技术路线

## 本次已实现

- 统一报告 API：`src/workbench_report.py`，提供 PCB / SMT / 综合 DFM 导出。
- 可复用后台调度：`src/ui/tasks.py`，工作线程只处理普通数据，通过队列回到 Tk 主线程。
- PCB / SMT 分析及报告导出从 Tk 回调中移出；运行时显示进度，防止重复提交同类任务。
- 分析开始前在主线程抓取输入快照；输入版本变化后丢弃过期计算结果。
- PCB / SMT 表格按每批 100 行渲染，避免一次插入全部数据。
- 报告先在目标目录的临时目录生成，成功后替换正式文件；综合 Excel 无需反复保存/读取子报告。
- 修正原 PCB 报告封面固定行号导致单板信息与问题汇总相互覆盖的问题。

后台线程改善的是交互响应，不等于算法本身计算更快。
Python 纯计算受 GIL 影响，复杂几何若仍显著影响交互，需以实板性能测量决定是否迁移至进程或本地计算库。

## 推荐长期架构：C# WPF 前端 + Python 后端

适合 Windows 桌面、中文业务表格、多窗口、坐标预览和主题定制。

```text
WPF / MVVM / 虚拟化 DataGrid / 2D 视图
               ↓ 异步请求与任务ID
独立 Python 工作进程（常驻，不每次启动）
               ↓ 普通 dict / 版本化 JSON
现有 BRD、PCB、SMT 分析及报告模块
```

现有 `src/dfm_engine.py`、`src/smt_engine.py`、`src/workbench_report.py`
没有 Tk 依赖，可作为后续前后端边界。复杂几何热点再根据性能数据选择 C# / C++ / Rust 实现。

### 通信合同（建议，尚未实现）

请求字段：`protocol_version / request_id / input_revision / operation / payload`。
响应字段：`request_id / input_revision / status / result / error`。
操作：`extract_brd / analyze_pcb / load_smt_profile / analyze_smt / export_report`。
UI 发起任务时保存输入版本，取消或换板后忽略旧响应；退出时终止自己创建的工作进程。
可以使用标准输入输出的 JSON 行协议，业务日志仅写标准错误，保持数据通道干净。
不是每个请求启动一个 Python 进程，也不在 WPF UI 线程同步等待。

### 迁移步骤

1. 用真实 BRD 记录解析、几何、分析、渲染和导出耗时，确认瓶颈。
2. 将已有无 UI 的接口封装为版本化工作进程协议，建立协议契约测试。
3. 实现 WPF 主窗口、PCB/SMT页、文件选择、动态字段表、风险与来源详情、三类报告按钮。
4. 增加 DataGrid 虚拟化、分页、后台进度和坐标视图；用当前黄金样板比对结果。
5. 统一安装/发布，Python 后端随应用私有打包；用户使用一个前端入口。

WPF 改善外观与交互，不能单靠换语言保证 BRD 解析/几何计算更快。
若希望保持 Python 全栈，PySide6/Qt 是可选方案，同样需后台任务与虚拟化模型。

## 验证

```powershell
python -B -m unittest tools.test_smt_engine tools.test_workbench_report tools.test_workbench_ui -v
```

覆盖：三个报告出口、完整内容与超链接、不同板结果拒绝合并、缺数据结论、
后台期间事件循环持续运行、过期结果丢弃及导出输入快照。
这是代码契约验证；WPF / Qt 迁移未在本次实现。
