#!/bin/bash

BASE_DIR="/home/gdaniel1979/my_projects/drive_sync"

cd "$BASE_DIR"
LOG_FILE="$BASE_DIR/drive_sync.log"
TEMP_FILE=$(mktemp)

/usr/bin/python3 "$BASE_DIR/drive_sync.py" > "$TEMP_FILE" 2>&1

if [ -f "$LOG_FILE" ]; then
    cat "$TEMP_FILE" "$LOG_FILE" > "$LOG_FILE.tmp" && mv "$LOG_FILE.tmp" "$LOG_FILE"
else
    mv "$TEMP_FILE" "$LOG_FILE"
fi

rm -f "$TEMP_FILE"