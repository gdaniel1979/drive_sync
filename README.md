
  # OneDrive → Google Drive Sync

A Python script that automatically copies specified files from personal Microsoft OneDrive to Google Drive. If a file already exists on Google Drive, it is overwritten. Designed to run unattended via cron.

---

## Features

- Syncs specific files from OneDrive to a Google Drive folder
- Overwrites existing files instead of creating duplicates
- Token caching for both Microsoft and Google — interactive login only required on first run
- Minimal one-line summary log per run (prepend, newest entry always on top)
- Bash wrapper script included for cron automation

