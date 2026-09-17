@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PATH=%~dp0bin;%~dp0runtime\python;%~dp0runtime\python\Scripts;%PATH%"
if not exist "%~dp0runtime\python\python.exe" (
  echo Chua cai dat. Hay chay setup.exe mot lan.
  pause
  exit /b 1
)
echo Dang mo Video Summarizer Pro...
"%~dp0runtime\python\python.exe" "%~dp0main.py"
if errorlevel 1 pause
