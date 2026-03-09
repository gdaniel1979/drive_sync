#!/bin/bash
LOG_FILE="/home/gdaniel1979/hobby_projects/drive_sync/drive_sync.log"
TEMP_FILE=$(mktemp)

/usr/bin/python3 /home/gdaniel1979/hobby_projects/drive_sync/onedrive_to_gdrive.py > "$TEMP_FILE" 2>&1

if [ -f "$LOG_FILE" ]; then
    cat "$TEMP_FILE" "$LOG_FILE" > "$LOG_FILE.tmp" && mv "$LOG_FILE.tmp" "$LOG_FILE"
else
    mv "$TEMP_FILE" "$LOG_FILE"
fi

rm -f "$TEMP_FILE"