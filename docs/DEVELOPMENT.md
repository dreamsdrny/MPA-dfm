# MPA-DFM 开发教程

本教程面向二次开发和本地联调：环境搭建、授权、目录职责、配置、测试、构建与发布。最终用户操作请看 [../TUTORIAL.md](../TUTORIAL.md)。

> 阅读顺序建议：先按第 1–4 节把程序跑起来，再看第 5 节配置自己需要的规则，改动代码前读第 6 节的边界约定。

## 1. 环境准备

| 项目 | 要求 |
|---|---|
| 操作系统 | Windows 10 / 11 x64（GUI 基于 Tk；发布脚本使用 PowerShell / 批处理） |
| Python | 3.12（`py -3.12`），使用仓库内 `.venv` |
| Git | 任意较新版本 |
| 图形界面 | Python 自带 Tk；可用 `python -m tkinter` 验证 |
| Cadence | 原生 Artwork / NC Drill / NC Route 导出、坐标联动需要已授权的 Cadence PCB Editor |
| ODB++ | `brd2odb.exe` 需要额外安装 ODB++ Inside 及许可 |
| Excel | 报告预览调用本机 Excel；无 Excel 时仍可导出 XLSX / CSV |

运行依赖见 `requirements.txt`，开发与构建依赖见 `requirements-dev.txt`（含 pytest、ruff、PyInstaller、Pillow）。

## 2. 获取与安装

```powershell
git clone https://github.com/dreamsdrny/MPA-dfm.git
cd MPA-dfm

py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

验证依赖与测试环境：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest tools -q
.\.venv\Scripts\python.exe -X utf8 main.py --help
```

`-X utf8` 用于避免 Windows 控制台代码页导致中文日志 / 报告路径乱码。

## 3. 启动与授权

### 3.1 为什么启动会失败

所有业务入口（`mpa_dfm.py`、`evan_dfm.py`、`main.py`、`gui.py`、`evan_brd.py`）都会先执行 `src/bootstrap.py`：

```text
日志 → 授权与签名配置校验 → 严格 YAML 运行配置 → Python / 原生依赖检查 → 创建业务窗口
```

仓库内的 `config/license_policy.json` 故意保持 `initialization_required: true` 且 `public_key` 为空，因此在公开仓库直接启动会得到：

```text
授权发行者尚未初始化。请先用授权管理工具初始化并构建正式程序。
Issuer not initialized; initialize and build deployment first.
```

这**不是**缺陷，也不应通过修改代码绕过。正式构建由发布脚本写入公钥、签名清单和绑定 EXE 哈希的 `license.json`。

### 3.2 本机开发用的 TEST 授权

`tools/prepare_test_deployment.py` 会生成一套临时 TEST 发行者（公钥、签名清单、30 天授权）。它只用于本机开发，**不能用于正式交付**。

```powershell
# 1) 生成临时部署目录
.\.venv\Scripts\python.exe -X utf8 tools\prepare_test_deployment.py --out output\dev_deployment

# 2) 把授权与签名配置放入源码根目录
Copy-Item output\dev_deployment\license.json .
Copy-Item output\dev_deployment\config\license_policy.json config\
Copy-Item output\dev_deployment\config\integrity_manifest.json config\

# 3) 启动 GUI
.\.venv\Scripts\python.exe -X utf8 mpa_dfm.py

# 4) 开发结束、提交前恢复仓库中的未初始化策略
git restore config/license_policy.json
Remove-Item config\integrity_manifest.json, license.json -ErrorAction SilentlyContinue
```

也可以不复制 `license.json`，改用 `--license` 指定路径：

```powershell
.\.venv\Scripts\python.exe -X utf8 mpa_dfm.py --license output\dev_deployment\license.json
```

注意：

- 生成的授权自当天起 30 天有效，过期后重新执行第 1 步。
- 若之后新增或修改了 `config/` 下的 YAML / JSON，必须**重新生成** TEST 部署，否则签名清单校验会失败。
- `config/integrity_manifest.json`、`license.json`、`expired_license.json` 已加入 `.gitignore`；`config/license_policy.json` 是跟踪文件，提交前务必恢复。
- 测试（`pytest tools`）不需要授权；只有真正启动业务入口才校验。

### 3.3 管理员命令行

| 参数 | 用途 |
|---|---|
| `--license <路径>` | 指定许可证文件 |
| `--check-deployment <结果JSON>` | 执行完整启动校验但不创建业务窗口，输出结果 JSON |
| `--check-trace-ui <结果JSON>` | 校验后实际创建主窗口与走线窗口，用于 UI 冒烟验收 |
| `--help` | 只显示用法，不执行授权检查 |

## 4. 运行入口

| 入口 | 说明 |
|---|---|
| `mpa_dfm.py` | 统一工作台入口（PCB / SMT / Checklist / Gerber），推荐 |
| `evan_dfm.py` | DFM 工作台；支持 `board.brd`、`--gerber <文件/目录/压缩包>`、`--license` |
| `main.py` | 兼容 CLI：`--demo`、`--dfm`、`--analyze`、`--pick`、`--export-skill` 等 |
| `gui.py` / `evan_brd.py` | 旧窗口兼容入口，同样经过启动校验 |

常用命令：

```powershell
# 直接打开某块 BRD
.\.venv\Scripts\python.exe -X utf8 evan_dfm.py "D:\PCB\board.brd"

# 直接分析 Gerber 目录（无需 BRD）
.\.venv\Scripts\python.exe -X utf8 evan_dfm.py --gerber "D:\PCB\gerber"

# 兼容 CLI 演示数据
.\.venv\Scripts\python.exe -X utf8 main.py --demo

# 只看 CLI 参数
.\.venv\Scripts\python.exe -X utf8 main.py --help
```

直接导入 `src.domain.*` / `src.application.*` 函数做脚本或测试，不等同于获得业务启动许可。

## 5. 配置体系

### 5.1 公开仓库不包含的配置

为保护业务数据，以下目录/文件被 `.gitignore` 排除，需要自备：

| 路径 | 用途 |
|---|---|
| `config/internal/application.yaml` | 运行配置（线程数、排队上限）；示例见 `config/internal/application.yaml.example` |
| `config/internal/pcb/pcb_design_rules.yaml` | 公司内部 PCB 设计基线 |
| `config/internal/pcb/pcb_object_rules.yaml` | 内部对象检查建议阈值 |
| `config/internal/smt/smt_design_rules.yaml` | 公司内部 SMT 设计基线 |
| `config/suppliers/pcb/pcb_capability_hongban.yaml` | 板厂制程能力（红板） |
| `config/suppliers/pcb/pcb_capability_sihui.yaml` | 板厂制程能力（四会） |
| `config/suppliers/smt/smt_capability_lijing.yaml` | SMT 供应商能力（立景） |

这些文件是生产数据，**不要提交到公开仓库**。首次使用可从历史交付包、板厂采集表或内部配置库获取，并确认修订日期与适用产线。缺失时：

- PCB 参数比对无法加载对应板厂能力库；
- SMT 相关测试会自动跳过（不会伪装成通过）；
- `config/internal/application.yaml` 缺失时使用内置默认值 `max_workers=4`、`max_pending=16`。

### 5.2 运行配置

```yaml
schema_version: 1
tasks:
  max_workers: 4      # 1..32
  max_pending: 16     # 1..1024
```

校验规则：只允许 `schema_version` 与 `tasks`；拒绝未知键、重复键、布尔冒充整数、越界值。文件存在时必须包含在签名完整性清单中，否则启动失败。

### 5.3 能力库的加载方式

`src/domain/pcb_rules.py` 使用固定映射：

```python
FACTORY_FILES = {
    "hongban": "suppliers/pcb/pcb_capability_hongban.yaml",
    "sihui": "suppliers/pcb/pcb_capability_sihui.yaml",
}
```

新增板厂需要同时修改 `FACTORY_FILES`、`FACTORY_LABELS`、`FACTORY_ALIASES` 及界面的板厂选项，并补充对应 YAML。每次分析都会记录配置文件路径、修订号和 SHA-256；能力哈希在分析前后不一致时丢弃结果。

SMT 能力通过界面导入 YAML，或在源码中调用 `src.smt_engine.load_smt_profile(path)`。YAML 必须声明 `schema_version: 1`，重复键会被拒绝。

### 5.4 其他配置与模板

| 路径 | 内容 |
|---|---|
| `config/checklist_auto_rules.yaml` | Checklist 自动填写规则（已公开） |
| `config/license_policy.json` | 发行者公钥策略；公开仓库保持未初始化 |
| `templates/` | 叠构、几何、Placement 示例，可作为资料格式参考 |
| `demo/` | 演示 BRD、DRC、Placement、Excellon 等样例 |

## 6. 代码结构与开发约定

### 6.1 分层与依赖方向

```text
入口（mpa_dfm.py / evan_dfm.py / main.py）
  └─ src/bootstrap.py            日志 → 授权 → 配置 → 依赖
       └─ src/ui/                只负责输入、任务提交、结果展示
            └─ src/application/  工作流与请求快照
                 ├─ src/domain/  纯规则与几何内核（不读文件、不依赖 Tk）
                 └─ src/adapters/ + src/infrastructure/  外部格式、工具、授权、I/O
```

硬性约束（由 `tools/test_layered_architecture.py` 检查）：

- `src/domain/` 只能依赖标准库、NumPy、Shapely 和领域模块，不读文件、不导入 Tk。
- `src/application/`、`src/adapters/`、`src/infrastructure/` 不得导入 `src.ui`。
- `src/ui/` 不直接调用格式读取器或几何引擎，只调用应用服务。
- 后台线程只接收普通值；Tk 变量必须在主线程先取值，再提交任务。
- 完成回调前核对输入版本（BRD 路径 / 大小 / mtime、文件 SHA-256、能力库哈希），过期结果丢弃。

兼容层 `src/services/` 和根目录平铺模块保留旧导入路径，不是另一套算法。

### 6.2 结论与证据

- 统一结论 `fail > review > pass`；缺数据、未确认工艺、部分覆盖均为 `review`，不自动放行。
- 每个结果保留 `_analysis` 摘要、逐项 `evidence`、来源和覆盖状态。
- `geometry_complete=False` 表示几何算法尚未覆盖全板，不能因为 `geometry_executed=True` 就宣称通过。
- 报告层不得“修正”结论；界面隐藏低优先级只影响展示，不改变计数与依据。

### 6.3 输入与输出安全

- 归档（ZIP / TAR / TGZ）读取有路径穿越、链接、重复路径和解压预算限制。
- 报告写入使用暂存目录 + 独占发布，失败回滚，不覆盖已有文件。
- CSV / Excel 输出对以 `= + - @` 开头的单元格做转义，避免公式注入。
- 源 BRD / BOM / 制造资料只读；产物写入独立输出目录。
- 不提交客户板卡、BOM、截图、交付包和任何私钥。

## 7. 测试

```powershell
# 全量回归（约 5 分钟；缺内部配置时 SMT 相关用例自动跳过）
.\.venv\Scripts\python.exe -X utf8 -m pytest tools -q

# 分层架构约束
.\.venv\Scripts\python.exe -X utf8 -m pytest tools\test_layered_architecture.py -q

# 单模块示例
.\.venv\Scripts\python.exe -X utf8 -m pytest tools\test_gerber_standalone.py -q
.\.venv\Scripts\python.exe -X utf8 -m pytest tools\test_smt_workflow.py -q

# 静态检查（只查未定义名等致命问题）
.\.venv\Scripts\python.exe -X utf8 -m ruff check src/application src/domain src/adapters src/infrastructure src/ui src/bootstrap.py --select F
```

说明：

- 合成测试数据不等同真实板卡联调；Cadence 相关行为仍需在目标机器用真实 BRD 验收。
- 新增规则时同时补 `tools/test_*.py`，并覆盖“缺数据不得判通过”的分支。
- 带 GUI 的测试需要可用的 Tk 环境；无桌面会话时可能跳过或失败。

## 8. 构建与发布

### 8.1 PyInstaller 配置

`MPA_DFM.spec` 是正式主程序配置，负责收集 `src.ui`、`src.services`、Gerbonara 数据文件、`skill/`、`templates/` 等资源；构建禁用 UPX。`Evan_BRD.spec` 是旧兼容程序配置，默认不纳入版本管理。

### 8.2 生成带授权的交付包

```powershell
.\.venv\Scripts\python.exe -X utf8 tools\build_release.py `
  --out "delivery\Evan_MPA_DFM_20261125" `
  --admin "$env:LOCALAPPDATA\EvanMPADFMReleaseAdmin\20261125" `
  --days 60
```

脚本会：

1. 在管理员目录生成或复用 Ed25519 私钥（Windows DPAPI 保护）；
2. 生成 `config/license_policy.json` 与签名的 `config/integrity_manifest.json`；
3. 调用 PyInstaller 构建 `Evan_MPA_DFM.exe`；
4. 签发绑定 EXE SHA-256 的 `license.json`（60 天 = `starts + 59`，到期当天仍有效）；
5. 生成 `release.json`、插件目录和同名 ZIP。

私钥目录**不能**位于交付目录内，也不能提交到仓库。丢失私钥将无法为同一 EXE 续期。

### 8.3 续期与验收

```powershell
# 续期
.\.venv\Scripts\python.exe -X utf8 tools\renew_release_license.py `
  --admin "$env:LOCALAPPDATA\EvanMPADFMReleaseAdmin\20261125" `
  --exe "delivery\Evan_MPA_DFM_20261125\Evan_MPA_DFM.exe" `
  --output "output\renewed_license.json" --start 2027-01-22 --days 60

# 发布验收（独立副本，不替换交付 license）
.\.venv\Scripts\python.exe -X utf8 tools\verify_release.py `
  --release "delivery\Evan_MPA_DFM_20261125" `
  --admin "$env:LOCALAPPDATA\EvanMPADFMReleaseAdmin\20261125" `
  --evidence "output\release_20261125"
```

发布前检查清单：

- `git status` 中没有客户资料、`delivery/`、`license.json`、`config/suppliers/`、`config/internal/` 的规则文件；
- 全量测试通过；
- 配置变更后重新构建并重新签发；
- 在目标 Windows 版本和真实板卡上验证 Gerber / BRD / SMT 关键路径。

## 9. 常见问题

| 现象 | 原因与处理 |
|---|---|
| `授权发行者尚未初始化` | `config/license_policy.json` 仍是未初始化状态；按 3.2 生成 TEST 授权，或使用正式构建包 |
| `运行配置未包含在签名完整性清单中` | 新增/修改了 `config/internal/application.yaml` 但未重新生成签名清单；重新生成 TEST 部署或重新构建 |
| `许可证...过期/哈希不匹配/签名无效` | 授权与当前 EXE 或日期不匹配；使用 `--license` 指向正确文件，或重新签发 |
| `ModuleNotFoundError: gerbonara/shapely/...` | 未安装依赖；执行 `pip install -r requirements-dev.txt` |
| `No module named tkinter` | Python 安装未包含 Tk；使用 python.org 官方安装包并勾选 Tcl/Tk |
| PCB 分析提示找不到板厂能力库 | 未放入 `config/suppliers/pcb/pcb_capability_*.yaml`，或板厂别名未注册 |
| SMT 测试被跳过 | 本机缺少 `config/suppliers/smt/` 或 `config/internal/smt/` 配置，属于预期行为 |
| 中文输出乱码 | 使用 `-X utf8` 运行；确认文件保存为 UTF-8 |
| 原生导出失败 | 检查 Cadence PCB Editor 版本 / 许可、Artwork film、`nctape.exe` / `ncroute.exe` / `brd2odb.exe` 路径 |
| 坐标跳转不准 | 核对设计单位、原点、镜像、BRD 版本；制造资料未证明对齐时不要跳转 |

日志位置：`%LOCALAPPDATA%\EvanMPA_DFM\logs\runtime.log`；打包程序的崩溃日志在 EXE 同目录 `Evan_DFM_crash.log`。

## 10. 数据安全与贡献

- 仓库采用 AGPL-3.0。提交前确认没有把公司内部基线、供应商阈值、客户板卡 / BOM、授权文件带入 diff。
- 新增配置样例请使用 `*.example`，并写入明显的占位说明。
- 不要通过修改授权校验、伪造 `geometry_complete` 或删除缺数据项来让结果“变绿”。
- 提交信息建议说明改动范围（入口 / 规则 / 界面 / 报告）以及新增测试。
