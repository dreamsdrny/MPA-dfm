"""Composition root: logging -> authorization -> configuration -> dependencies.

No business window is imported or created until this succeeds. License policy
and signed manifest validation remain mandatory, including diagnostic modes.
"""
import sys
from dataclasses import dataclass
from pathlib import Path

from src.adapters.configuration import RuntimeConfig, load_runtime_config
from src.infrastructure.dependencies import check_dependencies
from src.infrastructure.logging import configure_logging
from src.infrastructure.tasks import configure_executor


@dataclass(frozen=True)
class StartupContext:
    deployment: dict
    config: RuntimeConfig
    resources: Path
    dependencies: tuple[str, ...]


def prepare_startup(license_path=None):
    logger = configure_logging()
    logger.info("Startup checks started")
    try:
        from src.startup_guard import deployment_status
        deployment = deployment_status(license_path=license_path)
        base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
        resources = Path(getattr(sys, "_MEIPASS", base))
        runtime_path = resources / "config" / "internal" / "application.yaml"
        manifest = deployment.get("config_manifest")
        if (runtime_path.exists() and manifest is not None
                and "config/internal/application.yaml" not in manifest.get("files", {})):
            raise ValueError("运行配置未包含在签名完整性清单中，拒绝加载")
        if runtime_path.exists() and manifest is not None:
            from src.integrity import verify_checksum
            verify_checksum(runtime_path, manifest["files"]["config/internal/application.yaml"])
        config = load_runtime_config(resources)
        if runtime_path.exists() and manifest is not None:
            verify_checksum(runtime_path, manifest["files"]["config/internal/application.yaml"])
        dependencies = check_dependencies()
        configure_executor(config.max_workers, config.max_pending)
    except ImportError as exc:
        logger.exception("Startup dependency could not be imported")
        raise ValueError(f"启动依赖无法加载：{exc}；请检查运行环境或重新安装程序") from exc
    except Exception:
        logger.exception("Startup checks failed")
        raise
    logger.info("Startup checks completed")
    return StartupContext(deployment, config, resources, dependencies)
