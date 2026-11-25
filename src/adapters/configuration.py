"""Strict, bounded YAML loading for runtime settings."""
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RuntimeConfig:
    max_workers: int = 4
    max_pending: int = 16


def read_yaml(path, limit=1024 * 1024):
    import yaml

    from src.infrastructure.resources import read_bounded

    class UniqueLoader(yaml.SafeLoader):
        pass

    def mapping(loader, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            if key in result:
                raise ValueError(f"YAML 重复规则键/重复键：{key}")
            result[key] = loader.construct_object(value_node, deep=deep)
        return result

    UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
    try:
        data = yaml.load(read_bounded(path, limit).decode("utf-8-sig"), Loader=UniqueLoader)
    except yaml.YAMLError as exc:
        raise ValueError("YAML 配置无效") from exc
    if not isinstance(data, dict):
        raise ValueError("YAML 配置必须为对象")  # noqa: TRY004 配置文件格式错误
    return data


def load_runtime_config(resources):
    path = Path(resources) / "config" / "internal" / "application.yaml"
    if not path.exists():
        return RuntimeConfig()  # Compatible with already signed older deployments.
    data = read_yaml(path)
    if (set(data) - {"schema_version", "tasks"} or type(data.get("schema_version")) is not int
            or data.get("schema_version") != 1):
        raise ValueError("运行配置 schema_version 或字段无效")
    tasks = data.get("tasks", {})
    if not isinstance(tasks, dict) or set(tasks) - {"max_workers", "max_pending"}:
        raise ValueError("任务配置字段无效")
    defaults = RuntimeConfig()
    for key, maximum in (("max_workers", 32), ("max_pending", 1024)):
        value = tasks.get(key, getattr(defaults, key))
        if type(value) is not int or not 1 <= value <= maximum:
            raise ValueError(f"任务配置 {key} 必须为 1..{maximum} 的整数")
    return RuntimeConfig(**tasks)
