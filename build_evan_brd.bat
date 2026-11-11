@echo off
chcp 65001 >nul
echo ============================================
echo  MPA_DFM: PCB + SMT + Checklist (single application)
echo ============================================
echo.

for %%P in (MPA_DFM.exe) do (
    tasklist /FI "IMAGENAME eq %%P" 2>nul | find /I "%%P" >nul
    if not errorlevel 1 (
        echo [WARN] %%P is running. Closing it so the old build can be replaced...
        taskkill /IM %%P /F >nul 2>&1
    )
)
timeout /t 2 /nobreak >nul

pip show pyinstaller >nul 2>&1
if %errorlevel% neq 0 (
    echo Installing PyInstaller...
    pip install pyinstaller
)

echo.
echo Building MPA_DFM.exe (PCB + SMT + Checklist)...
python -m PyInstaller --clean --noconfirm "MPA_DFM.spec"
if %errorlevel% neq 0 goto failed

echo.
echo ============================================
echo  Build SUCCESS!
echo   dist\MPA_DFM.exe  (PCB + SMT + Checklist)
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
