from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from sales_analytics.config import MerchantConfig
from sales_analytics.db.models import BatchRun, Merchant, Order, Payment
from sales_analytics.services.csv_export_service import CsvExportService
from sales_analytics.services.drive_upload_service import DriveUploadService
from sales_analytics.services.ingestion_service import IngestionService
from sales_analytics.services.normalization_service import NormalizationService


@dataclass(frozen=True)
class BatchResult:
    run_id: str
    merchant_id: int
    business_date: date
    status: str
    orders_count: int
    payments_count: int
    csv_files_count: int
    uploaded_files_count: int


class BatchService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        ingestion: IngestionService,
        normalization: NormalizationService,
        csv_export: CsvExportService,
        drive_upload: DriveUploadService,
    ):
        self.session_factory = session_factory
        self.ingestion = ingestion
        self.normalization = normalization
        self.csv_export = csv_export
        self.drive_upload = drive_upload

    def run_for_merchant(self, merchant: MerchantConfig, business_date: date) -> BatchResult:
        with self.session_factory() as session:
            self._upsert_merchant(session, merchant)
            run = BatchRun(merchant_id=merchant.merchant_id, business_date=business_date)
            session.add(run)
            session.commit()

            try:
                raw_orders = self.ingestion.ingest(session, merchant, business_date)
                self.normalization.normalize_orders(
                    session, merchant.merchant_id, business_date, raw_orders
                )
                session.commit()
                orders_count, payments_count = self._count_normalized_rows(session, merchant.merchant_id, business_date)

                run.status = "SUCCESS"
                run.orders_count = orders_count
                run.payments_count = payments_count
                run.csv_files_count = 0
                run.drive_upload_status = "SKIPPED"
                run.finished_at = _now()
                session.commit()
                return BatchResult(
                    run_id=run.run_id,
                    merchant_id=merchant.merchant_id,
                    business_date=business_date,
                    status=run.status,
                    orders_count=orders_count,
                    payments_count=payments_count,
                    csv_files_count=0,
                    uploaded_files_count=0,
                )
            except Exception as exc:
                session.rollback()
                failed_run = session.get(BatchRun, run.run_id)
                if failed_run:
                    failed_run.status = "FAILED"
                    failed_run.error_message = str(exc)
                    failed_run.finished_at = _now()
                    session.commit()
                raise

    def has_successful_run(self, merchant_id: int, business_date: date) -> bool:
        with self.session_factory() as session:
            count = session.scalar(
                select(func.count(BatchRun.run_id)).where(
                    BatchRun.merchant_id == merchant_id,
                    BatchRun.business_date == business_date,
                    BatchRun.status == "SUCCESS",
                )
            )
            return bool(count)

    def _upsert_merchant(self, session: Session, merchant: MerchantConfig) -> None:
        row = session.get(Merchant, merchant.merchant_id) or Merchant(merchant_id=merchant.merchant_id)
        row.merchant_name = merchant.merchant_name
        row.business_number = merchant.business_number
        row.timezone = merchant.timezone
        row.business_open_time = merchant.business_open_time
        row.business_close_time = merchant.business_close_time
        row.drive_folder_id = merchant.drive_folder_id
        row.is_active = merchant.is_active
        session.merge(row)

    def _count_normalized_rows(self, session: Session, merchant_id: int, business_date: date) -> tuple[int, int]:
        orders = session.scalar(
            select(func.count(Order.order_id)).where(Order.merchant_id == merchant_id, Order.business_date == business_date)
        )
        payments = session.scalar(
            select(func.count(Payment.payment_id)).where(Payment.merchant_id == merchant_id, Payment.business_date == business_date)
        )
        return int(orders or 0), int(payments or 0)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
