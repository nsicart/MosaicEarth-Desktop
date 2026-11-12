@echo off
REM RealityCapture Post-Export Hook for MosaicEarth Cloud & 4D Surveys
REM Usage: rc_post_export_mosaicearth.bat "<ExportFolder>" ["<TaskName>"] ["<YYYY-MM-DD>"] ["<CloudProjectId>"]
setlocal
cd /d "%~dp0"

if "%~1"=="" (
    python -m mosaicearth_realitycapture.connector --gui
    exit /b %ERRORLEVEL%
)

set "EXPORT_DIR=%~1"
set "TASK_NAME=%~2"
set "SURVEY_DATE=%~3"
set "PROJECT_ID=%~4"
if "%PROJECT_ID%"=="" set "PROJECT_ID=0"

python -m mosaicearth_realitycapture.connector push-folder "%EXPORT_DIR%" --name "%TASK_NAME%" --survey-date "%SURVEY_DATE%" --project-id %PROJECT_ID%
