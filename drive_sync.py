#!/usr/bin/env python3

"""
OneDrive → Google Drive file copy script
- OneDrive access: Microsoft Graph API (OAuth2)
- Google Drive access: Google Drive API (OAuth2, personal account)
- If the file already exists on Google Drive: overwrite it
- Microsoft token cache: no need to log in every time it runs
- Skip upload if file hasn't changed since last run (lastModifiedDateTime)
"""

import os
import io
import json
import requests

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import msal
from datetime import datetime
from config import (
    MS_CLIENT_ID, MS_TENANT_ID, MS_TOKEN_CACHE_FILE,                 # Microsoft auth
    GOOGLE_CREDENTIALS_FILE, GOOGLE_TOKEN_FILE,                      # Google auth
    ONEDRIVE_FILES, GDRIVE_TARGET_FOLDER,                            # Drives info
    BREVO_API_KEY, EMAIL_SENDER, EMAIL_SENDER_NAME, EMAIL_RECEIVER   # E-mail sending info
)

# ============================================================
# MODIFIED CACHE FILE – stores last known lastModifiedDateTime
# per OneDrive file path
# ============================================================

MODIFIED_CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "last_modified_cache.json")

def load_modified_cache():
    """Load the last known modification timestamps from local JSON cache."""
    if os.path.exists(MODIFIED_CACHE_FILE):
        with open(MODIFIED_CACHE_FILE, "r") as f:
            return json.load(f)
    return {}

def save_modified_cache(cache):
    """Save modification timestamps to local JSON cache."""
    with open(MODIFIED_CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)

# ============================================================
# MICROSOFT GRAPH API – Authentication with token cache
# ============================================================

GRAPH_SCOPES = ["https://graph.microsoft.com/Files.Read"]

def load_ms_cache():
    """ Loading Microsoft token cache from file."""
    cache = msal.SerializableTokenCache()
    if os.path.exists(MS_TOKEN_CACHE_FILE):
        with open(MS_TOKEN_CACHE_FILE, "r") as f:
            cache.deserialize(f.read())
    return cache

def save_ms_cache(cache):
    """Saving Microsoft token cache into file if changed."""
    if cache.has_state_changed:
        with open(MS_TOKEN_CACHE_FILE, "w") as f:
            f.write(cache.serialize())

def get_ms_token():
    """
    Retrieve Microsoft token from cache or interactive login.
    Login required on first run, then automatic.
    """
    cache = load_ms_cache()
    app = msal.PublicClientApplication(
        MS_CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{MS_TENANT_ID}",
        token_cache=cache
    )

    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(GRAPH_SCOPES, account=accounts[0])
        if result and "access_token" in result:
            save_ms_cache(cache)
            return result["access_token"]
        else:
            print("⚠️ Token silent refresh failed, clearing cache.")
            open(MS_TOKEN_CACHE_FILE, "w").close()
    flow = app.initiate_device_flow(scopes=GRAPH_SCOPES)
    if "error" in flow:
        raise Exception(f"Device flow error: {flow.get('error')}: {flow.get('error_description')}")
    print("🔑 MICROSOFT LOGIN (only required the first time):")
    print(flow["message"])
    result = app.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        raise Exception(f"Microsoft authentication failed: {result.get('error_description')}")
    save_ms_cache(cache)
    print("✅ Microsoft authentication successful. Token saved to cache.")
    return result["access_token"]

def get_onedrive_file_metadata(token, onedrive_path):
    """Fetches file metadata from OneDrive, returns lastModifiedDateTime string."""
    headers = {"Authorization": f"Bearer {token}"}
    normalized = onedrive_path.replace("\\", "/")
    encoded = requests.utils.quote(normalized, safe="/")
    url = f"https://graph.microsoft.com/v1.0/me/drive/root:/{encoded}"
    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        data = response.json()
        return data.get("lastModifiedDateTime")
    elif response.status_code == 404:
        raise FileNotFoundError(f"Not found on OneDrive: {onedrive_path}")
    else:
        raise Exception(f"OneDrive metadata error ({response.status_code}): {response.text}")

def download_onedrive_file(token, onedrive_path):
    """Downloads a file from OneDrive in bytes format."""
    headers = {"Authorization": f"Bearer {token}"}
    normalized = onedrive_path.replace("\\", "/")
    encoded = requests.utils.quote(normalized, safe="/")
    url = f"https://graph.microsoft.com/v1.0/me/drive/root:/{encoded}:/content"
    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        return response.content
    elif response.status_code == 404:
        raise FileNotFoundError(f"Not found on OneDrive: {onedrive_path}")
    else:
        raise Exception(f"OneDrive download error ({response.status_code}): {response.text}")


# ============================================================
# GOOGLE DRIVE API – AUTH
# ============================================================

GDRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]

def get_gdrive_service():
    """Creating a Google Drive API service with OAuth2."""
    creds = None
    if os.path.exists(GOOGLE_TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(GOOGLE_TOKEN_FILE, GDRIVE_SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(GOOGLE_TOKEN_FILE, "w") as f:
                f.write(creds.to_json())
        else:
            raise Exception("Google token missing or invalid. Regenerate google_token.json.")
    return build("drive", "v3", credentials=creds)

def find_gdrive_folder_id(service, folder_name):
    """Searches for the folder ID with the specified name in the root of Google Drive."""
    safe_name = folder_name.replace("'", "\\'")
    query = (
        f"name='{safe_name}' and mimeType='application/vnd.google-apps.folder' "
        f"and 'root' in parents and trashed=false"
    )
    result = service.files().list(q=query, fields="files(id, name)").execute()
    files = result.get("files", [])
    return files[0]["id"] if files else None

def create_gdrive_folder(service, folder_name):
    """Creates a new folder in the root of Google Drive."""
    meta = {
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder"
    }
    folder = service.files().create(body=meta, fields="id").execute()
    print(f"  📁 Folder created on Google Drive: {folder_name}")
    return folder["id"]

def find_all_existing_files(service, filename, parent_id):
    clean_filename = filename.replace("\\", "/").split("/")[-1]
    safe_filename = clean_filename.replace("'", "\\'")
    if parent_id:
        query = f"name='{safe_filename}' and '{parent_id}' in parents and trashed=false"
    else:
        query = f"name='{safe_filename}' and 'root' in parents and trashed=false"
    result = service.files().list(q=query, fields="files(id, name)").execute()
    return [f["id"] for f in result.get("files", [])]

def upload_to_gdrive(service, filename, content, parent_id):
    import mimetypes
    clean_filename = filename.replace("\\", "/").split("/")[-1]

    mime_type, _ = mimetypes.guess_type(clean_filename)
    if not mime_type:
        mime_type = "application/octet-stream"

    safe_filename = clean_filename.replace("'", "\\'")
    if parent_id:
        query = f"name='{safe_filename}' and '{parent_id}' in parents and trashed=false"
    else:
        query = f"name='{safe_filename}' and 'root' in parents and trashed=false"
    result = service.files().list(q=query, fields="files(id, name)").execute()
    existing_ids = [f["id"] for f in result.get("files", [])]

    media = MediaIoBaseUpload(io.BytesIO(content), mimetype=mime_type, resumable=True)

    if existing_ids:
        service.files().update(
            fileId=existing_ids[0],
            media_body=media
        ).execute()
        for dup_id in existing_ids[1:]:
            service.files().delete(fileId=dup_id).execute()
    else:
        meta = {"name": clean_filename}
        if parent_id:
            meta["parents"] = [parent_id]
        service.files().create(body=meta, media_body=media, fields="id").execute()

def send_error_email(failed_files, error_msg=None):
    """Hibaértesítő email küldése Brevo API-n keresztül."""

    if error_msg:
        subject = "❌ drive_sync – critical error"
        body    = f"The drive_sync script crashed with the following error:\n\n{error_msg}"
    else:
        subject = "❌ drive_sync – upload failed"
        body    = "The following files failed to upload:\n\n" + "\n".join(failed_files)

    payload = {
        "sender":   {"name": EMAIL_SENDER_NAME, "email": EMAIL_SENDER},
        "to":       [{"email": EMAIL_RECEIVER}],
        "subject":  subject,
        "textContent": body
    }

    response = requests.post(
        "https://api.brevo.com/v3/smtp/email",
        headers={
            "api-key": BREVO_API_KEY,
            "Content-Type": "application/json"
        },
        json=payload
    )

    if response.status_code != 201:
        print(f"⚠️ Email sending failed: {response.status_code} {response.text}")
   
# ============================================================
# MAIN
# ============================================================

def main():
    try:
        ms_token   = get_ms_token()
        gdrive_svc = get_gdrive_service()

        parent_id = None
        if GDRIVE_TARGET_FOLDER:
            parent_id = find_gdrive_folder_id(gdrive_svc, GDRIVE_TARGET_FOLDER)
            if not parent_id:
                parent_id = create_gdrive_folder(gdrive_svc, GDRIVE_TARGET_FOLDER)

        modified_cache = load_modified_cache()
        success_files, failed_files, skipped_files = [], [], []

        for path in ONEDRIVE_FILES:
            filename = path.replace("\\", "/").split("/")[-1]
            try:
                last_modified = get_onedrive_file_metadata(ms_token, path)
                cached_modified = modified_cache.get(path)

                if last_modified and last_modified == cached_modified:
                    skipped_files.append(filename)
                    continue

                content = download_onedrive_file(ms_token, path)
                upload_to_gdrive(gdrive_svc, filename, content, parent_id)
                modified_cache[path] = last_modified
                success_files.append(filename)

            except Exception as e:
                failed_files.append(f"{filename} ({e})")

        save_modified_cache(modified_cache)

        timestamp   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        success_str = ", ".join(success_files) if success_files else "–"
        failed_str  = ", ".join(failed_files)  if failed_files  else "–"
        skipped_str = ", ".join(skipped_files) if skipped_files else "–"
                               
        if failed_files:
            print(f"{timestamp} | ❌ Failed: {failed_str} | ✅ Uploaded: {success_str} | ⏭️ Skipped: {skipped_str}")
            send_error_email(failed_files)
        else:
            print(f"{timestamp} | ✅ Uploaded: {success_str} | ❌ Failed: {failed_str} | ⏭️ Skipped: {skipped_str}")
    
    except Exception as e:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"{timestamp} | ⚠️ CRITICAL ERROR: {e}")
        send_error_email([], error_msg=str(e))

if __name__ == "__main__":
    main()