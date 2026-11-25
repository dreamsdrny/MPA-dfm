"""Stage complete output sets and publish exclusively after source verification."""
from pathlib import Path
from tempfile import TemporaryDirectory

from src.infrastructure.publication import publish_files


def _replace_paths(value, staging, target):
    if isinstance(value, dict):
        return {key: _replace_paths(item, staging, target) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_paths(item, staging, target) for item in value]
    if isinstance(value, tuple):
        return tuple(_replace_paths(item, staging, target) for item in value)
    if isinstance(value, str):
        return value.replace(str(staging), str(target))
    return value


def stage_outputs(directory, generate, verify=None, prepare=None):
    """generate(staging_directory) writes only within that directory."""
    target = Path(directory).resolve()
    target.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".dfm-output-", dir=target.parent) as temp:
        staged = Path(temp)
        result = generate(str(staged))
        if prepare:
            prepare(staged, target)
        pairs = [(path, target / path.relative_to(staged)) for path in staged.rglob("*") if path.is_file()]
        # Preserve relative links in JSON and text outputs after staging removal.
        for source, destination in pairs:
            if source.suffix.lower() in (".json", ".txt", ".html", ".csv", ".scr"):
                try:
                    text = source.read_text(encoding="utf-8")
                except UnicodeError:
                    continue  # Native CAD scripts may use mbcs encoding.
                text = text.replace(str(staged).replace("\\", "\\\\"), str(target).replace("\\", "\\\\"))
                text = text.replace(str(staged), str(target)).replace(staged.as_posix(), target.as_posix())
                source.write_text(text, encoding="utf-8")
            destination.parent.mkdir(parents=True, exist_ok=True)
        if verify:
            verify()
        publish_files(pairs)
        return _replace_paths(result, staged, target)
