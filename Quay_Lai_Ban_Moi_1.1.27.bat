@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ====================================================================
echo   DANG CHUYEN SANG BAN MOI 1.1.27 (CHONG QUET BAN QUYEN TIKTOK)...
echo ====================================================================
echo.

if not exist "backup\20260912-v1.1.27-tiktok-anticopyright\main.py" (
    echo [LOI] Khong tim thay ban luu 1.1.27 trong:
    echo backup\20260912-v1.1.27-tiktok-anticopyright\
    pause
    exit /b 1
)

copy /y "backup\20260912-v1.1.27-tiktok-anticopyright\*.*" "." >nul

echo ====================================================================
echo   DA CHUYEN THANH CONG SANG BAN MOI 1.1.27!
echo   - Day du tinh nang: Smart Shot Boundary, AI Xoa Logo, Scramble Audio.
echo ====================================================================
echo.
pause
