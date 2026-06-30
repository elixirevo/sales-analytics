from __future__ import annotations

import shutil
import time
import webbrowser
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import google.auth
from google.auth.exceptions import RefreshError
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

    def upload_period(
        self,
        merchant: MerchantConfig,
        period_start: date,
        period_type: str,
        local_path: Path,
        report_type: str,
    ) -> UploadedFile:
        return self.upload(merchant, period_start, local_path, report_type)

    def target_period_folder_id(self, merchant: MerchantConfig, period_start: date, period_type: str) -> str | None:
        return self.target_folder_id(merchant, period_start)


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

    def upload_period(
        self,
        merchant: MerchantConfig,
        period_start: date,
        period_type: str,
        local_path: Path,
        report_type: str,
    ) -> UploadedFile:
        folder = self._period_folder(merchant, period_start, period_type)
        folder.mkdir(parents=True, exist_ok=True)
        destination = folder / local_path.name
        shutil.copy2(local_path, destination)
        return UploadedFile(drive_file_id=str(destination), drive_folder_id=str(folder))

    def target_period_folder_id(self, merchant: MerchantConfig, period_start: date, period_type: str) -> str:
        return str(self._period_folder(merchant, period_start, period_type))

    def _period_folder(self, merchant: MerchantConfig, period_start: date, period_type: str) -> Path:
        base = self.root / f"merchant_{merchant.merchant_id}_{_safe_name(merchant.merchant_name)}" / f"{period_start:%Y}"
        if period_type == "daily":
            return base / f"{period_start:%m}" / period_start.isoformat()
        if period_type == "weekly":
            iso_year, iso_week, _ = period_start.isocalendar()
            return self.root / f"merchant_{merchant.merchant_id}_{_safe_name(merchant.merchant_name)}" / f"{iso_year:04d}" / "weekly" / f"{iso_year:04d}-W{iso_week:02d}"
        if period_type == "monthly":
            return base / f"{period_start:%m}"
        if period_type == "yearly":
            return base
        raise ValueError(f"Unsupported period_type={period_type}")


class DisabledDriveClient(DriveClient):
    def upload(self, merchant: MerchantConfig, business_date: date, local_path: Path, report_type: str) -> UploadedFile:
        return UploadedFile(drive_file_id=f"disabled:{local_path.name}", drive_folder_id="disabled")

    def upload_period(
        self,
        merchant: MerchantConfig,
        period_start: date,
        period_type: str,
        local_path: Path,
        report_type: str,
    ) -> UploadedFile:
        return UploadedFile(drive_file_id=f"disabled:{local_path.name}", drive_folder_id="disabled")


class GoogleDriveApiClient(DriveClient):
    def upload(self, merchant: MerchantConfig, business_date: date, local_path: Path, report_type: str) -> UploadedFile:
        folder_id = self.target_folder_id(merchant, business_date)
        if folder_id is None:
            raise RuntimeError("Google Drive target folder could not be resolved")
        return self._upload_or_update_csv(folder_id, local_path)

    def target_folder_id(self, merchant: MerchantConfig, business_date: date) -> str:
        return self._report_folder_id(merchant, business_date)

    def upload_period(
        self,
        merchant: MerchantConfig,
        period_start: date,
        period_type: str,
        local_path: Path,
        report_type: str,
    ) -> UploadedFile:
        folder_id = self.target_period_folder_id(merchant, period_start, period_type)
        if folder_id is None:
            raise RuntimeError("Google Drive target period folder could not be resolved")
        return self._upload_or_update_csv(folder_id, local_path)

    def target_period_folder_id(self, merchant: MerchantConfig, period_start: date, period_type: str) -> str:
        return self._period_folder_id(merchant, period_start, period_type)

    def _report_folder_id(self, merchant: MerchantConfig, business_date: date) -> str:
        parent_id = merchant.drive_folder_id or "root"
        merchant_folder = self._ensure_folder(parent_id, f"merchant_{merchant.merchant_id}_{_safe_name(merchant.merchant_name)}")
        year_folder = self._ensure_folder(merchant_folder, f"{business_date:%Y}")
        month_folder = self._ensure_folder(year_folder, f"{business_date:%m}")
        return self._ensure_folder(month_folder, business_date.isoformat())

    def _period_folder_id(self, merchant: MerchantConfig, period_start: date, period_type: str) -> str:
        parent_id = merchant.drive_folder_id or "root"
        merchant_folder = self._ensure_folder(parent_id, f"merchant_{merchant.merchant_id}_{_safe_name(merchant.merchant_name)}")
        iso_year, iso_week, _ = period_start.isocalendar()
        year_name = f"{iso_year:04d}" if period_type == "weekly" else f"{period_start:%Y}"
        year_folder = self._ensure_folder(merchant_folder, year_name)
        if period_type == "daily":
            month_folder = self._ensure_folder(year_folder, f"{period_start:%m}")
            return self._ensure_folder(month_folder, period_start.isoformat())
        if period_type == "weekly":
            weekly_folder = self._ensure_folder(year_folder, "weekly")
            return self._ensure_folder(weekly_folder, f"{iso_year:04d}-W{iso_week:02d}")
        if period_type == "monthly":
            return self._ensure_folder(year_folder, f"{period_start:%m}")
        if period_type == "yearly":
            return year_folder
        raise ValueError(f"Unsupported period_type={period_type}")

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

    def _upload_or_update_csv(self, folder_id: str, local_path: Path) -> UploadedFile:
        media = MediaFileUpload(str(local_path), mimetype="text/csv", resumable=local_path.stat().st_size > 5 * 1024 * 1024)
        existing_file_id = self._find_file_id(folder_id, local_path.name)
        if existing_file_id:
            updated = (
                self.service.files()
                .update(
                    fileId=existing_file_id,
                    body={"name": local_path.name},
                    media_body=media,
                    fields="id, parents",
                )
                .execute()
            )
            return UploadedFile(drive_file_id=updated["id"], drive_folder_id=folder_id)
        metadata = {"name": local_path.name, "parents": [folder_id]}
        created = self.service.files().create(body=metadata, media_body=media, fields="id, parents").execute()
        return UploadedFile(drive_file_id=created["id"], drive_folder_id=folder_id)

    def _find_file_id(self, folder_id: str, name: str) -> str | None:
        escaped_name = name.replace("\\", "\\\\").replace("'", "\\'")
        query = (
            f"name = '{escaped_name}' "
            f"and '{folder_id}' in parents "
            "and trashed = false"
        )
        result = (
            self.service.files()
            .list(
                q=query,
                spaces="drive",
                fields="files(id, name, modifiedTime)",
                pageSize=1,
                orderBy="modifiedTime desc",
            )
            .execute()
        )
        files = result.get("files", [])
        if not files:
            return None
        return files[0]["id"]


class GoogleDriveClient(GoogleDriveApiClient):
    def __init__(self, credentials_file: str):
        credentials = service_account.Credentials.from_service_account_file(credentials_file, scopes=DRIVE_SCOPES)
        self.service = build("drive", "v3", credentials=credentials, cache_discovery=False)


class GoogleOAuthDriveClient(GoogleDriveApiClient):
    def __init__(self, token_file: Path, client_secrets_file: str = "", auto_auth: bool = False):
        if auto_auth and not token_file.exists():
            if _running_in_container():
                raise ValueError(_missing_oauth_token_message(token_file))
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
    try:
        credentials = flow.run_local_server(host=host, port=port, prompt="consent", access_type="offline")
    except webbrowser.Error as exc:
        raise ValueError(
            "Google OAuth browser login could not be opened in this environment. "
            "Run `uv run sales-analytics auth-google` on your local machine, then mount the generated token file into Docker."
        ) from exc
    token_file.write_text(credentials.to_json(), encoding="utf-8")
    return token_file


def load_oauth_credentials(token_file: Path) -> Credentials:
    if not token_file.exists():
        raise ValueError(_missing_oauth_token_message(token_file))
    credentials = Credentials.from_authorized_user_file(str(token_file), DRIVE_SCOPES)
    try:
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            token_file.write_text(credentials.to_json(), encoding="utf-8")
    except RefreshError as exc:
        invalid_path = move_invalid_oauth_token(token_file)
        raise ValueError(_reauth_required_message(token_file, invalid_path)) from exc
    if not credentials.valid:
        invalid_path = move_invalid_oauth_token(token_file)
        raise ValueError(_reauth_required_message(token_file, invalid_path))
    return credentials


def move_invalid_oauth_token(token_file: Path) -> Path:
    invalid_path = _available_invalid_token_path(token_file)
    try:
        token_file.replace(invalid_path)
    except OSError:
        return token_file
    return invalid_path


def _available_invalid_token_path(token_file: Path) -> Path:
    invalid_path = token_file.with_name(f"{token_file.name}.invalid")
    if not invalid_path.exists():
        return invalid_path
    return token_file.with_name(f"{token_file.name}.invalid-{int(time.time())}")


def _reauth_required_message(token_file: Path, invalid_path: Path) -> str:
    return (
        "Google OAuth token has expired, been revoked, or is invalid. "
        f"The existing token was moved to {invalid_path}. "
        f"Run `uv run sales-analytics auth-google --token-file {token_file}` locally to sign in again, "
        "then restart the Docker container with the same data volume mounted."
    )


def _missing_oauth_token_message(token_file: Path) -> str:
    return (
        f"OAuth token file not found: {token_file}. "
        "Run `uv run sales-analytics auth-google` locally first, then mount the token file into Docker."
    )


def _running_in_container() -> bool:
    return Path("/.dockerenv").exists()


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
