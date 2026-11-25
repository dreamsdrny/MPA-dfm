@echo off
python -c "import json; p=json.load(open('config/license_policy.json')); assert p.get('public_key'), 'Initialize deployment using License Manager first'"
if errorlevel 1 exit /b 1
set EVAN_BUILD_NAME=Evan_MPA_DFM_Licensed
python -m PyInstaller --clean --noconfirm MPA_DFM.spec
if errorlevel 1 exit /b 1

set "LM_ROOT=%EVAN_LICENSE_MANAGER_ROOT%"
if not defined LM_ROOT set "LM_ROOT=D:\Evan\Softer-scret\Evan_License_Manager"
if exist "%LM_ROOT%\build_manager.bat" (
  call "%LM_ROOT%\build_manager.bat"
  if errorlevel 1 exit /b 1
) else (
  echo [WARN] License Manager build skipped: "%LM_ROOT%\build_manager.bat" not found.
)
