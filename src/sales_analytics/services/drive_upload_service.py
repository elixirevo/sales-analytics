from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from sales_analytics.clients.google_drive_client import DriveClient
from sales_analytics.config import MerchantConfig
from sales_analytics.db.models import DriveUpload, now_utc
from sales_analytics.services.csv_export_service import ExportedFile


class DriveUploadService:
    def __init__(self, client: DriveClient):
        self.client = client

    def upload_files(
        self,
        session: Session,
        run_id: str,
        merchant: MerchantConfig,
        business_date: date,
        files: list[ExportedFile],
    ) -> int:
        uploaded_count = 0
        for file in files:
            target_folder_id = self.client.target_folder_id(merchant, business_date)
            existing = session.scalar(
                select(DriveUpload).where(
                    DriveUpload.merchant_id == merchant.merchant_id,
                    DriveUpload.business_date == business_date,
                    DriveUpload.report_type == file.report_type,
                    DriveUpload.checksum == file.checksum,
                )
            )
            if existing and _is_remote_upload(existing.drive_file_id) and (
                target_folder_id is None or existing.drive_folder_id == target_folder_id
            ):
                uploaded_count += 1
                continue
            upload = self.client.upload(merchant, business_date, file.path, file.report_type)
            if existing:
                existing.run_id = run_id
                existing.file_name = file.path.name
                existing.drive_file_id = upload.drive_file_id
                existing.drive_folder_id = upload.drive_folder_id
                existing.file_size_bytes = file.size_bytes
                existing.checksum = file.checksum
                existing.status = "SUCCESS"
                existing.uploaded_at = now_utc()
                session.flush()
                uploaded_count += 1
                continue
            session.add(
                DriveUpload(
                    run_id=run_id,
                    merchant_id=merchant.merchant_id,
                    business_date=business_date,
                    report_type=file.report_type,
                    file_name=file.path.name,
                    drive_file_id=upload.drive_file_id,
                    drive_folder_id=upload.drive_folder_id,
                    file_size_bytes=file.size_bytes,
                    checksum=file.checksum,
                    status="SUCCESS",
                )
            )
            session.flush()
            uploaded_count += 1
        return uploaded_count

    def upload_period_files(
        self,
        session: Session,
        run_id: str,
        merchant: MerchantConfig,
        period_start: date,
        period_type: str,
        files: list[ExportedFile],
    ) -> int:
        uploaded_count = 0
        for file in files:
            target_folder_id = self.client.target_period_folder_id(merchant, period_start, period_type)
            existing = session.scalar(
                select(DriveUpload).where(
                    DriveUpload.merchant_id == merchant.merchant_id,
                    DriveUpload.business_date == period_start,
                    DriveUpload.report_type == file.report_type,
                    DriveUpload.checksum == file.checksum,
                )
            )
            if existing and _is_remote_upload(existing.drive_file_id) and (
                target_folder_id is None or existing.drive_folder_id == target_folder_id
            ):
                uploaded_count += 1
                continue
            upload = self.client.upload_period(merchant, period_start, period_type, file.path, file.report_type)
            if existing:
                existing.run_id = run_id
                existing.file_name = file.path.name
                existing.drive_file_id = upload.drive_file_id
                existing.drive_folder_id = upload.drive_folder_id
                existing.file_size_bytes = file.size_bytes
                existing.checksum = file.checksum
                existing.status = "SUCCESS"
                existing.uploaded_at = now_utc()
                session.flush()
                uploaded_count += 1
                continue
            session.add(
                DriveUpload(
                    run_id=run_id,
                    merchant_id=merchant.merchant_id,
                    business_date=period_start,
                    report_type=file.report_type,
                    file_name=file.path.name,
                    drive_file_id=upload.drive_file_id,
                    drive_folder_id=upload.drive_folder_id,
                    file_size_bytes=file.size_bytes,
                    checksum=file.checksum,
                    status="SUCCESS",
                )
            )
            session.flush()
            uploaded_count += 1
        return uploaded_count


def _is_remote_upload(drive_file_id: str) -> bool:
    return not (
        drive_file_id.startswith("uploads/")
        or drive_file_id.startswith("/app/uploads/")
        or drive_file_id.startswith("disabled:")
    )
