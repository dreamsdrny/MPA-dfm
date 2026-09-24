@echo off
echo ============================================
echo  Layout-brd: Building executable (evan.ico)...
echo ============================================
echo.

pip show pyinstaller >nul 2>&1
if %errorlevel% neq 0 (
    echo Installing PyInstaller...
    pip install pyinstaller
)

echo Cleaning old build (Layout-brd only)...
if exist "build\Layout-brd" rmdir /s /q "build\Layout-brd"
if exist "dist\Layout-brd"  rmdir /s /q "dist\Layout-brd"

echo.
echo Building with PyInstaller...
python -m PyInstaller --clean --noconfirm "Layout-brd.spec"

if %errorlevel% equ 0 (
    echo.
    echo ============================================
    echo  Build SUCCESS!
    echo  Output: dist\Layout-brd\Layout-brd.exe
    echo ============================================
) else (
    echo.
    echo ============================================
    echo  Build FAILED!
    echo ============================================
)
pause