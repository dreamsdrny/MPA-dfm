@echo off
chcp 65001 >nul
setlocal

python -c "import json; p=json.load(open('config/license_policy.json')); assert p.get('public_key'), 'Initialize deployment using License Manager first'"
if errorlevel 1 exit /b 1

rem Build a unique name for this run. Old EXEs in dist are never overwritten or deleted.
for /f %%i in ('python -c "import datetime;print(datetime.datetime.now().strftime('%%Y%%m%%d_%%H%%M%%S'))"') do set "BUILD_STAMP=%%i"
if not defined BUILD_STAMP (
    echo [ERROR] Could not create a build timestamp. Check that python is available.
    exit /b 1
)

set "EVAN_BUILD_NAME=Evan_MPA_DFM_Licensed"
set "EVAN_BUILD_STAMP=%BUILD_STAMP%"
python -m PyInstaller --clean --noconfirm MPA_DFM.spec
if errorlevel 1 exit /b 1

echo.
echo Built dist\Evan_MPA_DFM_Licensed_%BUILD_STAMP%.exe
echo Previous builds are kept in the dist folder.

set "LM_ROOT=%EVAN_LICENSE_MANAGER_ROOT%"
if not defined LM_ROOT set "LM_ROOT=D:\Evan\Softer-scret\Evan_License_Manager"
if exist "%LM_ROOT%\build_manager.bat" (
  call "%LM_ROOT%\build_manager.bat"
  if errorlevel 1 exit /b 1
) else (
  echo [WARN] License Manager build skipped: "%LM_ROOT%\build_manager.bat" not found.
)
