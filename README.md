# MPA-DFM

**制造可行性分析平台** · Manufacturing Capability Analysis Platform

面向 PCB / SMT 设计资料的离线 DFM（可制造性）分析工具：读取 Cadence Allegro `.brd`、Gerber / Excellon、ODB++、BOM / Placement，按可配置的设计基线与供应商制程能力逐项比对，输出带来源、坐标和覆盖状态的问题清单与 Excel / CSV / JSON 报告。

> **仓库范围说明**
>
> 本仓库公开发布源码、文档与测试。为避免泄露业务数据，以下内容**不在**公开仓库中：
>
> - `config/internal/` 中的公司内部设计基线与规则
> - `config/suppliers/` 中的真实供应商制程能力阈值
> - `delivery/` 历史交付包、客户板卡 / BOM 资料、授权私钥与 `license.json`
>
> 首次运行前请按 [开发教程的配置章节](docs/DEVELOPMENT.md#5-配置体系) 放入自己的配置，并按 [启动与授权](docs/DEVELOPMENT.md#3-启动与授权) 准备本机开发授权。

## 主要能力

| 模块 | 说明 |
|---|---|
| Gerber 独立工作台 | 直接导入单文件、整套目录或 ZIP / TAR / TGZ，无需先有 BRD；支持底片预览、问题坐标与报告导出 |
| BRD DFM 分析 | 解析 Cadence Allegro `.brd` 的约束、叠构、器件、孔 / 焊盘 / 走线对象，按板厂能力逐项比对 |
| 参数与叠构校验 | 线宽 / 线距 / 孔环 / 阻焊桥等参数按成品铜厚和内层叠构分档检查，未确认项不判通过 |
| 实际几何 / 对象检查 | Gerber、NC Drill、ODB++ 的铜层几何与对象检查，记录测量值、来源、覆盖缺口 |
| SMT 能力分析 | 设计值与产线过程值分离，支持供应商能力 YAML、内部基线对比和已确认对象轮廓检查 |
| 制造资料协同 | Gerber / ODB++ 与 BOM / Placement 双向核对，保留双方原记录和版本差异 |
| 报告输出 | XLSX / CSV / TXT / JSON，包含结论、逐项能力、问题坐标、器件坐标、来源和覆盖状态 |
| Allegro 联动 | 生成坐标定位脚本与走线筛选插件（`skill/imp_highlight.il`），通过原生 SKILL 与 PCB Editor 交互 |
| Checklist / BRD 审查 | 兼容入口提供 Checklist 自动填写、八大类 BRD 审查与资料库（`store-brd` / `store-bom`）配对 |

分析结论遵循 `fail > review > pass`：缺数据、未确认工艺、部分几何覆盖都不自动放行。`geometry_complete=False` 表示当前几何算法尚未覆盖全板，不能把“任务完成”当成制造通过。

## 目录结构

```text
MPA-DFM/
├─ mpa_dfm.py / evan_dfm.py    统一工作台入口：启动校验 → GUI
├─ main.py / gui.py / evan_brd.py
│                              兼容 CLI 与旧窗口入口（同样执行启动校验）
├─ src/
│  ├─ bootstrap.py             组合根：日志 → 授权 → 配置 → 依赖检查
│  ├─ domain/                  纯领域规则（不依赖 Tk / 文件系统）
│  ├─ application/             工作流与请求快照
│  ├─ adapters/                BRD / Gerber / ODB++ / BOM 读取与报告写入
│  ├─ infrastructure/          授权、快照、有界任务、日志、归档、发布
│  ├─ services/                兼容服务导出
│  ├─ ui/                      Tk 界面、TaskRunner 与过期结果拒绝
│  ├─ design_inputs/           多格式资料适配与协同
│  └─ brd_analysis/            BRD 八大类分析能力目录
├─ config/                     运行配置（内部基线与供应商能力需自备）
├─ docs/                       架构、验收、Gerber / SMT / 走线筛选说明
├─ demo/                       演示 BRD、DRC、Placement 等样例
├─ skill/                      Allegro 走线筛选插件（.il / .form）
├─ templates/                  叠构、几何、Placement 模板
├─ tools/                      测试、构建、发布与验收脚本
└─ TUTORIAL.md                 使用教程
```

更细的依赖方向与调用合同见 [分层架构](docs/LAYERED_ARCHITECTURE.md) 和 [架构说明](docs/ARCHITECTURE.md)。

## 快速开始（源码）

环境要求：Windows 10/11 x64、Python 3.12、Git。GUI 依赖 Tk；CAD 原生导出与坐标联动需要本机安装并授权 Cadence PCB Editor（ODB++ 导出另需 ODB++ Inside）。

```powershell
git clone https://github.com/dreamsdrny/MPA-dfm.git
cd MPA-DFM
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt

# 跑测试（无需授权）
.\.venv\Scripts\python.exe -X utf8 -m pytest tools -q

# 查看兼容 CLI 用法（--help 不执行授权检查）
.\.venv\Scripts\python.exe -X utf8 main.py --help
```

启动图形界面需要有效的授权与签名配置（仓库内的 `config/license_policy.json` 故意保持未初始化状态）。本机开发可用 TEST 发行者生成临时授权：

```powershell
.\.venv\Scripts\python.exe -X utf8 tools\prepare_test_deployment.py --out output\dev_deployment
Copy-Item output\dev_deployment\license.json .
Copy-Item output\dev_deployment\config\license_policy.json config\
Copy-Item output\dev_deployment\config\integrity_manifest.json config\
.\.venv\Scripts\python.exe -X utf8 mpa_dfm.py
```

TEST 授权只能用于本机开发，不能用于正式交付。详细步骤、恢复方法和常见错误见 [开发教程](docs/DEVELOPMENT.md)。

## 文档

| 文档 | 内容 |
|---|---|
| [TUTORIAL.md](TUTORIAL.md) | **使用教程**：安装与授权、Gerber / BRD / SMT 操作、坐标定位、报告与常见问题 |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | **开发教程**：环境、授权、目录职责、配置、测试、构建与发布 |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 既有算法、数据可靠性与边界 |
| [docs/LAYERED_ARCHITECTURE.md](docs/LAYERED_ARCHITECTURE.md) | 分层架构、请求合同与迁移结果 |
| [docs/ACCEPTANCE_TESTS.md](docs/ACCEPTANCE_TESTS.md) | 已执行测试与待实机验收事项 |
| [docs/GERBER_STANDALONE.md](docs/GERBER_STANDALONE.md) | Gerber 独立分析流程与限制 |
| [docs/SMT_TESTING.md](docs/SMT_TESTING.md) | SMT 能力、对象检测与测试说明 |
| [docs/PCB_TRACE_FILTER.md](docs/PCB_TRACE_FILTER.md) | Allegro 走线筛选插件操作说明 |
| [docs/IMP_QUICK_START.md](docs/IMP_QUICK_START.md) | 阻抗 / 走线高亮快速上手 |

## 构建与发布

正式交付使用 PyInstaller 构建，并由独立管理员目录中的 Ed25519 私钥签发授权：

```powershell
.\.venv\Scripts\python.exe -X utf8 tools\build_release.py `
  --out "delivery\Evan_MPA_DFM_20261125" `
  --admin "$env:LOCALAPPDATA\EvanMPADFMReleaseAdmin\20261125" --days 60
```

- 构建会生成签名的 `config/integrity_manifest.json`、绑定 EXE SHA-256 的 `license.json`、`release.json` 和同名 ZIP。
- 私钥由 Windows DPAPI 保护，保存在管理员目录，**不进入仓库和交付包**。
- 续期使用 `tools/renew_release_license.py`，发布验收使用 `tools/verify_release.py`。
- 修改配置后必须重新构建并重新签发，不能只替换交付目录里的文件。

## 已知边界

- 当前不提供完整的全网表跨层开短路证明、完整最小线宽骨架、全部焊盘栈 / 公差 / 装配可达性检查。
- 未确认的孔工艺、制造资料对齐、供应商门槛或缺数据项一律阻止自动放行。
- Gerber / ODB++ 解析覆盖常用子集，不等同商业 CAM 的全格式语义。
- Cadence 原生导出、坐标联动、ODB++ 转换需要在目标机器与真实板卡上验收。

本工具用于设计阶段筛查和评审辅助，不能替代板厂 CAM 检查、DRC 签核或最终制造放行。

## 许可证

[GNU AGPL-3.0](LICENSE)。
