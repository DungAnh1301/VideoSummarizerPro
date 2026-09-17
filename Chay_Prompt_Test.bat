@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PATH=%~dp0bin;%~dp0runtime\python;%~dp0runtime\python\Scripts;%PATH%"
if exist "%~dp0runtime\python\python.exe" (
  "%~dp0runtime\python\python.exe" "%~dp0prompt_test_lab.py"
) else if exist "%~dp0build\payload\runtime\python\python.exe" (
  "%~dp0build\payload\runtime\python\python.exe" "%~dp0prompt_test_lab.py"
) else (
  py "%~dp0prompt_test_lab.py"
)
if errorlevel 1 pause
