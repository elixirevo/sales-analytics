from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import google.auth
from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from sales_analytics.config import MerchantConfig, Settings

DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]


@dataclass(frozen=True)
class UploadedFile:
    drive_file_id: str
    drive_folder_id: str


class DriveClient:
    def upload(self, merchant: MerchantConfig, business_date: date, local_path: Path, report_type: str) -> UploadedFile:
        raise NotImplementedError

    def target_folder_id(self, merchant: MerchantConfig, business_date: date) -> str | None:
        return None


class LocalDriveClient(DriveClient):
    def __init__(self, root: Path):
        self.root = root

    def upload(self, merchant: MerchantConfig, business_date: date, local_path: Path, report_type: str) -> UploadedFile:
        folder = (
            self.root
            / f"merchant_{merchant.merchant_id}_{_safe_name(merchant.merchant_name)}"
            / f"{business_date:%Y}"
            / f"{business_date:%m}"
            / business_date.isoformat()
        )
        folder.mkdir(parents=True, exist_ok=True)
        destination = folder / local_path.name
        shutil.copy2(local_path, destination)
        return UploadedFile(drive_file_id=str(destination), drive_folder_id=str(folder))


class DisabledDriveClient(DriveClient):
    def upload(self, merchant: MerchantConfig, business_date: date, local_path: Path, report_type: str) -> UploadedFile:
        return UploadedFile(drive_file_id=f"disabled:{local_path.name}", drive_folder_id="disabled")


class GoogleDriveApiClient(DriveClient):
    def upload(self, merchant: MerchantConfig, business_date: date, local_path: Path, report_type: str) -> UploadedFile:
        folder_id = self.target_folder_id(merchant, business_date)
        if folder_id is None:
            raise RuntimeError("Google Drive target folder could not be resolved")
        metadata = {"name": local_path.name, "parents": [folder_id]}
        media = MediaFileUpload(str(local_path), mimetype="text/csv", resumable=local_path.stat().st_size > 5 * 1024 * 1024)
        created = self.service.files().create(body=metadata, media_body=media, fields="id, parents").execute()
        return UploadedFile(drive_file_id=created["id"], drive_folder_id=folder_id)

    def target_folder_id(self, merchant: MerchantConfig, business_date: date) -> str:
        return self._report_folder_id(merchant, business_date)

    def _report_folder_id(self, merchant: MerchantConfig, business_date: date) -> str:
        parent_id = merchant.drive_folder_id or "root"
        merchant_folder = self._ensure_folder(parent_id, f"merchant_{merchant.merchant_id}_{_safe_name(merchant.merchant_name)}")
        year_folder = self._ensure_folder(merchant_folder, f"{business_date:%Y}")
        month_folder = self._ensure_folder(year_folder, f"{business_date:%m}")
        return self._ensure_folder(month_folder, business_date.isoformat())

    def _ensure_folder(self, parent_id: str, name: str) -> str:
        escaped_name = name.replace("\\", "\\\\").replace("'", "\\'")
        query = (
            "mimeType = 'application/vnd.google-apps.folder' "
            f"and name = '{escaped_name}' "
            f"and '{parent_id}' in parents "
            "and trashed = false"
        )
        result = self.service.files().list(q=query, spaces="drive", fields="files(id, name)", pageSize=1).execute()
        files = result.get("files", [])
        if files:
            return files[0]["id"]
        metadata = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent_id],
        }
        created = self.service.files().create(body=metadata, fields="id").execute()
        return created["id"]


class GoogleDriveClient(GoogleDriveApiClient):
    def __init__(self, credentials_file: str):
        credentials = service_account.Credentials.from_service_account_file(credentials_file, scopes=DRIVE_SCOPES)
        self.service = build("drive", "v3", credentials=credentials, cache_discovery=False)


class GoogleOAuthDriveClient(GoogleDriveApiClient):
    def __init__(self, token_file: Path, client_secrets_file: str = "", auto_auth: bool = False):
        if auto_auth and not token_file.exists():
            create_oauth_token(client_secrets_file, token_file)
        credentials = load_oauth_credentials(token_file)
        self.service = build("drive", "v3", credentials=credentials, cache_discovery=False)


class GoogleApplicationDefaultDriveClient(GoogleDriveApiClient):
    def __init__(self):
        credentials, _ = google.auth.default(scopes=DRIVE_SCOPES)
        self.service = build("drive", "v3", credentials=credentials, cache_discovery=False)


def create_oauth_token(client_secrets_file: str, token_file: Path, host: str = "127.0.0.1", port: int = 0) -> Path:
    if not client_secrets_file:
        raise ValueError(
            "GOOGLE_OAUTH_CLIENT_SECRETS_FILE is required for OAuth authorization. "
            "Download a Desktop app OAuth client JSON from Google Cloud Console and set its local path in .env."
        )
    if not Path(client_secrets_file).exists():
        raise ValueError(f"Google OAuth client secrets file not found: {client_secrets_file}")
    token_file.parent.mkdir(parents=True, exist_ok=True)
    flow = InstalledAppFlow.from_client_secrets_file(client_secrets_file, DRIVE_SCOPES)
    credentials = flow.run_local_server(host=host, port=port, prompt="consent", access_type="offline")
    token_file.write_text(credentials.to_json(), encoding="utf-8")
    return token_file


def load_oauth_credentials(token_file: Path) -> Credentials:
    if not token_file.exists():
        raise ValueError(
            f"OAuth token file not found: {token_file}. "
            "Run `sales-analytics auth-google` locally first, then mount the token file into Docker."
        )
    credentials = Credentials.from_authorized_user_file(str(token_file), DRIVE_SCOPES)
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        token_file.write_text(credentials.to_json(), encoding="utf-8")
    if not credentials.valid:
        raise ValueError("OAuth credentials are invalid. Re-run `sales-analytics auth-google`.")
    return credentials


def build_drive_client(settings: Settings) -> DriveClient:
    if settings.upload_mode == "google":
        if not settings.google_credentials_file:
            raise ValueError("GOOGLE_APPLICATION_CREDENTIALS is required when UPLOAD_MODE=google")
        return GoogleDriveClient(settings.google_credentials_file)
    if settings.upload_mode == "google_oauth":
        return GoogleOAuthDriveClient(
            settings.google_oauth_token_file,
            client_secrets_file=settings.google_oauth_client_secrets_file,
            auto_auth=settings.google_oauth_auto_auth,
        )
    if settings.upload_mode == "google_adc":
        return GoogleApplicationDefaultDriveClient()
    if settings.upload_mode == "disabled":
        return DisabledDriveClient()
    return LocalDriveClient(settings.local_drive_dir)


def _safe_name(value: str) -> str:
    return value.replace("/", "_").replace(" ", "_")
