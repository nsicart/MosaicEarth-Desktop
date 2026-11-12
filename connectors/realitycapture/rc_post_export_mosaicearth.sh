#!/usr/bin/env sh
# RealityCapture / CLI Post-Export Hook for MosaicEarth Cloud & 4D Surveys
set -eu
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
if [ "$#" -eq 0 ]; then
    PYTHONPATH="$SCRIPT_DIR${PYTHONPATH:+:$PYTHONPATH}" exec python3 -m mosaicearth_realitycapture.connector --gui
fi
EXPORT_DIR="$1"
TASK_NAME="${2:-}"
SURVEY_DATE="${3:-}"
PROJECT_ID="${4:-0}"
PYTHONPATH="$SCRIPT_DIR${PYTHONPATH:+:$PYTHONPATH}" exec python3 -m mosaicearth_realitycapture.connector push-folder "$EXPORT_DIR" --name "$TASK_NAME" --survey-date "$SURVEY_DATE" --project-id "$PROJECT_ID"
