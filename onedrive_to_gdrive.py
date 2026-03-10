#!/usr/bin/env python3

"""
OneDrive → Google Drive file copy script
- OneDrive access: Microsoft Graph API (OAuth2)
- Google Drive access: Google Drive API (OAuth2, personal account)
- If the file already exists on Google Drive: overwrite it
- Microsoft token cache: no need to log in every time it runs
"""

import os
import io
import requests

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import msal
from datetime import datetime
from config import (
    MS_CLIENT_ID, MS_TENANT_ID, MS_TOKEN_CACHE_FILE, 
    GOOGLE_CREDENTIALS_FILE, GOOGLE_TOKEN_FILE, 
    ONEDRIVE_FILES, GDRIVE_TARGET_FOLDER
)

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

    # Let's try to get a token from the cache
    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(GRAPH_SCOPES, account=accounts[0])
        if result and "access_token" in result:
            save_ms_cache(cache)
            # print("✅ Microsoft token loaded from cache.")
            return result["access_token"]
        else:
            print("⚠️ Token silent refresh failed, clearing cache.")
            open(MS_TOKEN_CACHE_FILE, "w").close()
    flow = app.initiate_device_flow(scopes=GRAPH_SCOPES)
    if "error" in flow:
        raise Exception(f"Device flow error: {flow.get('error')}: {flow.get('error_description')}")
    print("\n🔑 MICROSOFT LOGIN (only required the first time):")
    print(flow["message"])
    result = app.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        raise Exception(f"Microsoft authentication failed: {result.get('error_description')}")
    save_ms_cache(cache)
    print("✅ Microsoft authentication successful. Token saved to cache.")
    return result["access_token"]

def download_onedrive_file(token, onedrive_path):
    """Downloads a file from OneDrive in bytes format."""
    headers = {"Authorization": f"Bearer {token}"}
    normalized = onedrive_path.replace("\\", "/")
    encoded = requests.utils.quote(normalized, safe="/")
    url = f"https://graph.microsoft.com/v1.0/me/drive/root:/{encoded}:/content"
    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        # print(f"  📥 Downloaded from OneDrive: {onedrive_path}")
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
            print("✅ Google token renewed.")
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                GOOGLE_CREDENTIALS_FILE, GDRIVE_SCOPES
            )
            flow.redirect_uri = "urn:ietf:wg:oauth:2.0:oob"
            auth_url, _ = flow.authorization_url(prompt="consent")
            print("\n🔑 GOOGLE LOGIN (only required the first time):")
            print(f"Nyisd meg ezt a linket a böngészőben:\n{auth_url}")
            code = input("\nThen paste the code you received here: ")
            flow.fetch_token(code=code)
            creds = flow.credentials
            print("✅ Google authentication successful. Token saved.")

        with open(GOOGLE_TOKEN_FILE, "w") as f:
            f.write(creds.to_json())

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

    # Find all existing files with the same name
    safe_filename = clean_filename.replace("'", "\\'")
    if parent_id:
        query = f"name='{safe_filename}' and '{parent_id}' in parents and trashed=false"
    else:
        query = f"name='{safe_filename}' and 'root' in parents and trashed=false"
    result = service.files().list(q=query, fields="files(id, name)").execute()
    existing_ids = [f["id"] for f in result.get("files", [])]

    media = MediaIoBaseUpload(io.BytesIO(content), mimetype=mime_type, resumable=True)

    if existing_ids:
        # Overwriting the first copy
        service.files().update(
            fileId=existing_ids[0],
            media_body=media
        ).execute()
        # print(f"  ♻️  Overwritten: {clean_filename}")
        # Delete other duplicates
        for dup_id in existing_ids[1:]:
            service.files().delete(fileId=dup_id).execute()
            # print(f"  🗑️  Duplicate deleted: {clean_filename}")
    else:
        meta = {"name": clean_filename}
        if parent_id:
            meta["parents"] = [parent_id]
        service.files().create(body=meta, media_body=media, fields="id").execute()
        # print(f"  ✅ Uploaded: {clean_filename}")

    
# ============================================================
# MAIN
# ============================================================

def main():
    ms_token   = get_ms_token()
    gdrive_svc = get_gdrive_service()

    parent_id = None
    if GDRIVE_TARGET_FOLDER:
        parent_id = find_gdrive_folder_id(gdrive_svc, GDRIVE_TARGET_FOLDER)
        if not parent_id:
            parent_id = create_gdrive_folder(gdrive_svc, GDRIVE_TARGET_FOLDER)

    success_files, failed_files = [], []

    for path in ONEDRIVE_FILES:
        filename = path.replace("\\", "/").split("/")[-1]
        try:
            content = download_onedrive_file(ms_token, path)
            upload_to_gdrive(gdrive_svc, filename, content, parent_id)
            success_files.append(filename)
        except Exception as e:
            failed_files.append(f"{filename} ({e})")

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    success_str = ", ".join(success_files) if success_files else "–"
    failed_str  = ", ".join(failed_files)  if failed_files  else "–"
    print(f"{timestamp} | Successfully uploaded: {success_str} | Unsuccessful: {failed_str}")

if __name__ == "__main__":
    main()
