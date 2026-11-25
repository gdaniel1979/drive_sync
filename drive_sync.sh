#!/bin/bash

BASE_DIR="/home/gdaniel1979/my_projects/drive_sync"
MAX_LOG_LINES=5000

cd "$BASE_DIR"
LOG_FILE="$BASE_DIR/drive_sync.log"
TEMP_FILE=$(mktemp)

# a projekt saját Python-környezete (3.12, uv; 2026-11-25 óta, független a rendszer Pythonjától)
/home/gdaniel1979/.venvs/drive-sync-py312/bin/python "$BASE_DIR/drive_sync.py" > "$TEMP_FILE" 2>&1
STATUS=$?

# Newest entries go on top; keep only the first MAX_LOG_LINES lines.
if [ -f "$LOG_FILE" ]; then
    cat "$TEMP_FILE" "$LOG_FILE" | head -n "$MAX_LOG_LINES" > "$LOG_FILE.tmp" && mv "$LOG_FILE.tmp" "$LOG_FILE"
else
    mv "$TEMP_FILE" "$LOG_FILE"
fi

rm -f "$TEMP_FILE"
exit $STATUS
