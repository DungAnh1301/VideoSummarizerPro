@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ====================================================================
echo   DANG KHOI PHUC DU AN VE PHIEN BAN CU 1.1.26 (CHUA SUA MA NGUON)...
echo ====================================================================
echo.

if not exist "release\VideoSummarizerPro_Update_1.1.26.zip" (
    echo [LOI] Khong tim thay file goc: release\VideoSummarizerPro_Update_1.1.26.zip
    pause
    exit /b 1
)

:: Luu lai ban 1.1.27 hien tai vao backup truoc khi ghi de de khong bao gio mat code
if not exist "backup\20260912-v1.1.27-tiktok-anticopyright" mkdir "backup\20260912-v1.1.27-tiktok-anticopyright"
copy /y scene_mapper.py "backup\20260912-v1.1.27-tiktok-anticopyright\" >nul 2>&1
copy /y editor_processor.py "backup\20260912-v1.1.27-tiktok-anticopyright\" >nul 2>&1
copy /y ai_processor.py "backup\20260912-v1.1.27-tiktok-anticopyright\" >nul 2>&1
copy /y antigravity_processor.py "backup\20260912-v1.1.27-tiktok-anticopyright\" >nul 2>&1
copy /y config_manager.py "backup\20260912-v1.1.27-tiktok-anticopyright\" >nul 2>&1
copy /y config.json "backup\20260912-v1.1.27-tiktok-anticopyright\" >nul 2>&1
copy /y main.py "backup\20260912-v1.1.27-tiktok-anticopyright\" >nul 2>&1
copy /y version.json "backup\20260912-v1.1.27-tiktok-anticopyright\" >nul 2>&1

echo [*] Dang giai nen de ban 1.1.26 tu goi zip goc...
powershell -Command "Expand-Archive -Path 'release\VideoSummarizerPro_Update_1.1.26.zip' -DestinationPath '.' -Force"

echo.
echo ====================================================================
echo   DA KHOI PHUC THANH CONG 100%% VE BAN CU 1.1.26!
echo   - Toan bo code da tro ve trang thai cu nguyen ban.
echo   - Code ban 1.1.27 da duoc cat an toan trong:
echo     backup\20260912-v1.1.27-tiktok-anticopyright\
echo ====================================================================
echo.
pause
