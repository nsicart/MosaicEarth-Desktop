@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "PYTHONPATH=%SCRIPT_DIR%;%PYTHONPATH%"
if "%~1"=="" (
    python -m mosaicearth_postshot.connector --gui
) else (
    python -m mosaicearth_postshot.connector push "%~1" %2 %3 %4 %5 %6 %7 %8
)
