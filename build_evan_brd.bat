@echo off
chcp 65001 >nul
setlocal

echo ============================================
echo  MPA_DFM: PCB + SMT + Checklist (single application)
echo ============================================
echo.

rem Build a unique name for this run. Old EXEs in dist are never overwritten or deleted.
for /f %%i in ('python -c "import datetime;print(datetime.datetime.now().strftime('%%Y%%m%%d_%%H%%M%%S'))"') do set "BUILD_STAMP=%%i"
if not defined BUILD_STAMP (
    echo [ERROR] Could not create a build timestamp. Check that python is available.
    goto failed
)
set "EVAN_BUILD_STAMP=%BUILD_STAMP%"
set "OUT_EXE=dist\MPA_DFM_%BUILD_STAMP%.exe"

python -m pip show pyinstaller >nul 2>&1
if %errorlevel% neq 0 (
    echo Installing PyInstaller...
    python -m pip install pyinstaller
    if errorlevel 1 goto failed
)

echo.
echo Building %OUT_EXE% (PCB + SMT + Checklist)...
python -m PyInstaller --clean --noconfirm "MPA_DFM.spec"
if %errorlevel% neq 0 goto failed

if not exist "%OUT_EXE%" (
    echo [ERROR] Expected output not found: %OUT_EXE%
    goto failed
)

echo.
echo ============================================
echo  Build SUCCESS!
echo   %OUT_EXE%
echo   Previous builds are kept in the dist folder.
echo ============================================
pause
exit /b 0

:failed
echo.
echo ============================================
echo  Build FAILED! (see the log above)
echo ============================================
pause
exit /b 1
