# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules, collect_data_files
import os
import json
from pathlib import Path

config_source=Path(os.environ.get('EVAN_CONFIG_DIR','config'))
issuer=json.loads((config_source/'license_policy.json').read_text(encoding='utf-8'))
hook=Path('build')/'evan_issuer_runtime.py'
hook.parent.mkdir(parents=True,exist_ok=True)
hook.write_text('import sys\nsys._evan_issuer_public = '+repr(issuer.get('public_key',''))+'\n',encoding='utf-8')

a = Analysis(
    ['mpa_dfm.py'], pathex=[], binaries=[],
    datas=[(os.environ.get('EVAN_CONFIG_DIR','config'), 'config'), ('skill', 'skill'), ('evan.ico', '.'), ('evan.png', '.'), ('evan_taskbar.png', '.')] + collect_data_files('gerbonara'),
    hiddenimports=collect_submodules('src.ui') + collect_submodules('src.brd_analysis') + collect_submodules('src.services')
                  + collect_submodules('src.application') + collect_submodules('src.domain')
                  + collect_submodules('src.adapters') + collect_submodules('src.infrastructure'),
    hookspath=[], hooksconfig={}, runtime_hooks=[str(hook)],
    excludes=['lxml', 'src.fab_data_reader', 'src.fab_viewer'],
    noarchive=False, optimize=0,
)
pyz = PYZ(a.pure)
# 每次构建生成新的文件名：设置 EVAN_BUILD_STAMP 后追加时间戳，
# 避免 PyInstaller 覆盖 dist 中已有的历史 EXE。未设置时保持旧名称不变。
base_name = os.environ.get('EVAN_BUILD_NAME', 'MPA_DFM')
build_stamp = os.environ.get('EVAN_BUILD_STAMP', '').strip()
build_name = f"{base_name}_{build_stamp}" if build_stamp else base_name
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name=build_name,
           debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          upx_exclude=[], runtime_tmpdir=None, console=False,
          disable_windowed_traceback=False, icon=['evan.ico'])
