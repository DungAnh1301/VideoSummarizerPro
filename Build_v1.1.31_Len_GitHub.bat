@echo off
chcp 65001 >nul
title Đang Build và Phát hành Video Summarizer Pro v1.1.42...
cd /d "%~dp0"

echo ============================================================
echo 🚀 Bắt đầu Build và Phát hành bản v1.1.42 lên GitHub...
echo ============================================================
echo.

powershell -ExecutionPolicy Bypass -File .\build_update.ps1 -Version 1.1.42 -Publish -Notes "v1.1.42: Cai tien lam mo trong suot (Frosted gblur), quet ban quyen chuan xac 2s/anh, bao ve mat/than nguoi choi (Action Zone Guard)"

if %ERRORLEVEL% EQU 0 (
    echo.
    echo ============================================================
    echo ✅ Xuất bản thành công v1.1.42 lên GitHub!
    echo ============================================================
) else (
    echo.
    echo ============================================================
    echo ❌ Có lỗi xảy ra trong quá trình build/publish.
    echo ============================================================
)

echo.
pause
