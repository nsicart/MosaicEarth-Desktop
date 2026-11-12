@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "PYTHONPATH=%SCRIPT_DIR%;%PYTHONPATH%"
if "%~1"=="" (
    python -m mosaicearth_arcgispro.connector --gui
) else (
    python -m mosaicearth_arcgispro.connector %*
)
