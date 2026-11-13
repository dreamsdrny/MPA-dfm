# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules, collect_data_files
import os

a = Analysis(
    ['mpa_dfm.py'], pathex=[], binaries=[],
    datas=[('config', 'config'), ('evan.ico', '.'), ('evan.png', '.'), ('evan_taskbar.png', '.')] + collect_data_files('gerbonara'),
    hiddenimports=collect_submodules('src.ui') + collect_submodules('src.brd_analysis'),
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['lxml', 'PIL', 'src.fab_data_reader', 'src.fab_viewer'],
    noarchive=False, optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name=os.environ.get('EVAN_BUILD_NAME', 'MPA_DFM'),
          debug=False, bootloader_ignore_signals=False, strip=False, upx=True,
          upx_exclude=[], runtime_tmpdir=None, console=False,
          disable_windowed_traceback=False, icon=['evan.ico'])
