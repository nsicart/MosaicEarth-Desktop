@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "PYTHONPATH=%SCRIPT_DIR%;%PYTHONPATH%"
if "%~1"=="" (
    python -m mosaicearth_djiterra.connector --gui
) else (
    python -m mosaicearth_djiterra.connector push-folder "%~1" %2 %3 %4 %5 %6
)
