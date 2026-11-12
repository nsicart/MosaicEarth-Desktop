@echo off
setlocal
cd /d "%~dp0"
python -m mosaicearth_cloudcompare.bridge %*
