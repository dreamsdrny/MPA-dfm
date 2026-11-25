# 分层架构实现（2026-11-25）

本次实现的是**可运行的增量架构**，不是重写所有算法或仅创建空目录。
PCB / SMT / Checklist / BRD 多能力 / 制造资料协同及辅助窗口均已接入应用服务。
保留既有算法、数据合同、授权机制和旧导入路径，避免破坏未提交的业务修改。

## 1. 启动与调用方向

```text
mpa_dfm.py → evan_dfm.main
  └─ src/bootstrap.py
       ├─ 每用户滚动日志
       ├─ 授权、公钥固定和签名配置检查（原 startup_guard）
       ├─ 严格 YAML 运行配置、任务资源上限
       └─ Python / 原生依赖加载检查
            ↓ 校验成功后才导入、创建业务窗口
src/ui/                              收集输入、提交任务、显示状态
            ↓
src/application/                     工作流与请求快照
  ├─ pcb_analysis.py                 PCB 导入/分析
  ├─ smt_analysis.py                 SMT 能力与对象证据工作流
  ├─ checklist.py                    Checklist 模板检查/填写
  ├─ brd_analysis.py                 BRD 多能力目录/分析
  ├─ manufacturing.py                制造资料/BOM 协同、ODB 对象分析
  └─ reports.py                      综合、BRD、制造协同报告/预览
            ↓
src/domain/                          不依赖 Tk、文件、YAML、CAD
  ├─ models.py                       来源/版本/单位/可信度/覆盖/统一结果
  ├─ results.py                      旧结果转换为统一复核与放行摘要
  ├─ pcb_rules.py / pcb_issues.py     PCB 能力/风险/缺失数据规则
  ├─ smt_rules.py / smt_objects.py    SMT 数值、适用性及对象判定
  ├─ checklist_rules.py              Checklist 内容匹配与事实判定
  ├─ checklist_decisions.py          Checklist 决策与覆盖统计
  ├─ layout_geometry.py              空间索引、距离、孔环与铜密度内核
  ├─ layout_checks.py / pcb_*.py      布局、孔型、焊盘/过孔/开窗规则
  ├─ brd_*.py / pad_*.py              BRD DRC/物理/电气与焊盘判定
  ├─ bom_rules.py                    位号双向核对与属性冲突
  ├─ manufacturing_rules.py          坐标证据合并和面别/DNP 冲突
  └─ verdict.py / combined_verdict.py 唯一结论与综合结论规则
            ↓ 外部数据与执行由应用层通过适配器提供
src/adapters/
  ├─ readers.py                      BRD/Gerber/ODB++/BOM/Excel
  ├─ brd_reader.py / *_reader.py      实际 BRD/ODB++/BOM/Excel 文件解析
  ├─ smt_evidence.py / drill_inputs.py 读取已验证对象证据与钻孔资料
  ├─ *_report.py                     Excel/CSV/JSON/TXT/HTML 格式写入
  ├─ cad.py                          Allegro/SKILL/原生制造工具
  ├─ configuration.py                有上限、拒绝重复键的 YAML
  └─ reports.py                      现有 Excel/CSV/JSON/TXT 等写入器
            ↓
src/infrastructure/
  ├─ snapshots.py                    文件/目录 SHA-256 快照、请求深拷贝
  ├─ resources.py                    既有文件读取与解析预算边界
  ├─ tasks.py                        有界后台任务执行，不调用 Tk
  ├─ logging.py                      2 MiB × 4 的滚动运行日志
  ├─ dependencies.py                 必要模块与原生库可加载性
  ├─ archives.py / resource_limits.py 安全归档与文件读取预算
  ├─ authorization.py / diagnostics.py 授权入口与错误记录
  ├─ paths.py / preferences.py        程序路径、界面偏好持久化
  └─ staging.py / publication.py      暂存、独占发布、竞争保护、失败回滚
```

领域层只导入标准库、纯计算库（NumPy/Shapely）与领域模块，不读取文件。
应用、适配器和基础设施不得导入 UI。UI 不直接导入领域引擎、格式读取器或外部工具。
UI 的 `TaskRunner` 只负责 Tk 事件轮询、生命周期与完成回调；执行线程由基础设施管理。
关闭窗口会丢弃回调，**不代表已终止正在运行的 CAD 或 I/O**。
工作线程完成后释放任务引用，不把 UI 闭包留在阻塞线程中。Tk 窗口活动期间
循环垃圾回收由主线程触发，避免工作线程析构旧 Tk 变量。

## 2. 请求与结果合同

所有服务接收普通值。可变字典/列表在应用服务执行时深拷贝；UI 必须在提交前
捕获 Tk 变量，不能把控件或 `StringVar` 传入 worker。PCB 请求来自
`application.contracts.PCBAnalysisRequest`，旧 `services.pcb_analysis` 重导出同一类型。

`domain.models` 提供：

- `InputSource`：来源路径、格式、版本、SHA-256。
- `Evidence`：字段值、单位、来源、`unknown/candidate/confirmed` 可信度。
- `Coverage`：总数、已判定数量、缺失项、是否完整；无数据不能声明完整。
- `AnalysisSummary`：服务类型、`fail/review/pass`、覆盖、依据、来源、范围与放行标志。

服务保留原结果字典，并新增 `_analysis` 统一摘要及逐项 `evidence`，避免破坏 UI 和报告合同。
`fail > review > pass`；缺数据、人工待评审、部分几何覆盖均不能自动放行。
原有报告里的“所选定量能力匹配”不是完整制造放行。SMT、BRD 多能力、Checklist、
BOM 关联目前不能证明完整制造覆盖，因此统一摘要保持保守复核，不伪造 `geometry_complete`。

```python
from src.application.brd_analysis import BRDAnalysisRequest, run_brd_analysis
from src.application.reports import export_brd_report

request = BRDAnalysisRequest(
    brd_path=r"D:\PCB\board.brd",
    out_dir=r"D:\PCB\analysis-output",
    export_files=(r"D:\PCB\drc.rpt",),
)
result = run_brd_analysis(request)
print(result["_analysis"]["verdict"])
export_brd_report(result, request.out_dir)
```

显式输入在分析前后校验内容版本；报告继续使用原有来源检查。
PCB/SMT 的 BRD、制造资料、规则配置校验不因新增包装而被取消。
Checklist 在暂存目录计算、生成文件，复核全部输入/规则版本后独占发布。
BRD 报告、制造资料协同报告、资料库报告、器件/网络输出采用同一暂存发布机制。
BRD 多能力中原生/预览制造产物仍可能在最终检查前生成；拒收过期结果不意味着
删除已运行外部工具的产物。自动发现的 CAD 导出候选在分析前纳入哈希快照，
其他能力额外搜索的 STEP/MCAD 等旁路文件仍需要各自适配器的覆盖合同。

## 3. 启动与配置

```powershell
cd D:\Evan\MPA-DFM\MPA-DFM
.\.venv\Scripts\python.exe -X utf8 mpa_dfm.py
```

运行仍要求有效授权及签名配置；未初始化的发行者会被拒绝，**未增加授权绕过开关**。
`main.py`、`gui.py`、`evan_brd.py` 的实际业务启动同样执行 `prepare_startup`。
根 `main.py` 在加载业务模块前预检；应用 CLI 在解析参数后再次校验，保证
从兼容程序直接调用 CLI 时同样没有未授权业务入口。
CLI 新增 `--license`；`--help` 仅显示用法，不执行业务。直接导入应用/领域函数
用于测试和内部调用，不是启动许可的替代入口。
Allegro/ODB 原生转换器是功能级可选依赖，使用对应功能时由原有适配器验证，
不是所有机器启动的必选依赖。

`config/internal/application.yaml`：

```yaml
schema_version: 1
tasks:
  max_workers: 4
  max_pending: 16
```

未知键、重复键、布尔冒充整数、超界上限均拒绝。
配置若存在，必须包含在授权验证后的签名完整性清单内；旧发行包没有该文件时
使用兼容默认值。任务队列满时向界面明确报告，不无限增建线程。

运行日志位于 `%LOCALAPPDATA%/EvanMPA_DFM/logs/runtime.log`，崩溃日志仍用
`diagnostics.py`。配置变更后必须重新签名构建，不修改旧交付的许可或私钥。
`--check-deployment` 同样执行完整启动检查，不只检查许可证。
PyInstaller 主配置已经包含四个新包；发布管理仍属于 `tools/`，不进入领域层。

## 4. 迁移结果与边界

**已完成：**启动组合根、六类应用服务、UI 调用边界、PCB/SMT/Checklist 实际规则、
几何对象内核、BRD DRC/物理/电气规则、BOM 协同规则、读取与输出适配器、
版本/哈希/安全归档/预算/日志/有界任务/暂存发布基础设施、架构约束测试。

**兼容入口：**`src/services/`、平铺旧模块和 `design_inputs/` 对已迁移实现
保留导出或模块身份映射，旧导入和 mock 目标继续有效。它们不是另一套算法。
旧根目录 `main.py` 只负责 CLI 启动，流程现位于 `application/legacy_cli.py`。

**实施边界：**部分 Gerber 格式扫描及 BRD 装配/MCAD/数据库导出器仍同时
生成业务明细和格式产物，保留在适配器而不是伪称纯领域函数。公共几何、
焊盘/过孔判定、覆盖与放行策略均由领域模块提供。
本次是架构重构，不增加完整网络 DRC、CAD 格式全语义支持或制造覆盖证明。
不删除源 BRD/BOM、存档、旧交付，也不改变 CAD 工具的运行边界。

## 5. 验证

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest tools/test_layered_architecture.py -q
.\.venv\Scripts\python.exe -X utf8 -m pytest tools -q
.\.venv\Scripts\python.exe -X utf8 -m ruff check src/application src/domain src/adapters src/infrastructure src/ui src/bootstrap.py tools/test_layered_architecture.py --select F
```

自动测试不等同目标机器 Allegro/真实 BRD 联调或新 EXE 授权交付验收。

架构测试同时约束：领域层仅依赖领域/纯计算库、领域函数不打开文件、UI 不直接
导入读取器/领域引擎、不直接读写业务文件、缺失/非有限数不判通过、旧入口授权
失败不创建窗口、报告发布不覆盖已有文件。旧器件/网络自检的合成 BRD 已更新为
当前有效 Header/设计对象合同，器件测试使用独立临时目录，不再清理工作区夹具目录。
