@echo off
REM Rebuild the single-file engine and verify it, before pushing to GitHub.
REM Usage: double-click, or run `rebuild.bat` in this folder.
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"

set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

echo [1/3] build single-file engine ...
"%PY%" engine\build_bundle.py
if errorlevel 1 goto fail

echo.
echo [2/3] check bundle is in sync with engine\*.py ...
"%PY%" engine\build_bundle.py --check
if errorlevel 1 goto fail

echo.
echo [3/3] selftest T1-T23c ...
"%PY%" skills\chemcritic-comparability\scripts\chemcritic_engine.py --selftest
if errorlevel 1 goto fail

echo.
echo ALL OK - now commit and push, otherwise the sandbox keeps the old version.
exit /b 0

:fail
echo.
echo FAILED - do NOT push yet.
exit /b 1
