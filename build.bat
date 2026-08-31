@echo off
echo ============================================
echo  BRD-AI: Building executable...
echo ============================================
echo.

REM Check if PyInstaller is installed
pip show pyinstaller >nul 2>&1
if %errorlevel% neq 0 (
    echo Installing PyInstaller...
    pip install pyinstaller
)

echo Cleaning old build...
if exist "build" rmdir /s /q "build"
if exist "dist"  rmdir /s /q "dist"

echo.
echo Building with PyInstaller...
python -m PyInstaller --clean --noconfirm ^
    --name="BRD-AI" ^
    --windowed ^
    --add-data="config/checklist_rules.yaml;config" ^
    --add-data="config/dfm_capability.yaml;config" ^
    --hidden-import=openpyxl ^
    --hidden-import=openpyxl.cell ^
    --hidden-import=openpyxl.styles ^
    --hidden-import=yaml ^
    --hidden-import=jinja2 ^
    --hidden-import=jinja2.ext ^
    --hidden-import=src.pcb_reader ^
    --hidden-import=src.signal_classifier ^
    --hidden-import=src.rule_engine ^
    --hidden-import=src.dfm_engine ^
    --hidden-import=src.skill_generator ^
    --hidden-import=src.report_generator ^
    --hidden-import=src.checklist_reader ^
    gui.py

if %errorlevel% equ 0 (
    echo.
    echo ============================================
    echo  Build SUCCESS!
    echo  Output: dist\BRD-AI\BRD-AI.exe
    echo ============================================
) else (
    echo.
    echo ============================================
    echo  Build FAILED!
    echo ============================================
)
pause