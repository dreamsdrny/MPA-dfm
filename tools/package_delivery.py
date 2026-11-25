"""Archive complete source and acceptance artifacts, excluding production secrets/user data."""
import json
import os
import sys
import zipfile
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.integrity import checksums


def main():
    root=Path(__file__).resolve().parents[1]
    destination=root/"delivery"
    destination.mkdir(exist_ok=True)
    source=destination/"Evan_source_20261113.zip"
    excludes={".git",".venv","__pycache__","build","dist","delivery",".pytest_cache",".ruff_cache",
              "output","store-brd","store-bom","tmp_compfixture","data","存档"}
    with zipfile.ZipFile(source,"w",zipfile.ZIP_DEFLATED) as archive:
        for path in root.rglob("*"):
            relative=path.relative_to(root)
            if path.is_file() and not any(part in excludes for part in relative.parts) and path.suffix.lower() not in (".evankey",".log",".brd"):
                archive.write(path,relative.as_posix())
    manager=root/"dist"/"Evan_License_Manager.exe"
    if not manager.is_file():
        manager=Path(os.environ.get("EVAN_LICENSE_MANAGER_ROOT",r"D:\Evan\Softer-scret\Evan_License_Manager"))/"bin"/"Evan_License_Manager.exe"
    files=[source,manager]+list((destination/"test_app").glob("*"))
    records={}
    for path in files:
        if path.is_file():
            try:name=str(path.relative_to(root))
            except ValueError:name=str(path)
            records[name]=checksums(path)
    (destination/"checksums.json").write_text(json.dumps(records,indent=2),encoding="utf-8")
    print("Source archive",source)


if __name__=="__main__":main()
