from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import select

from sales_analytics.cli import _run_discovered_bootstrap_backfill
from sales_analytics.clients.google_drive_client import LocalDriveClient
from sales_analytics.clients.toss_place_client import MockTossPlaceClient
from sales_analytics.config import MerchantConfig
from sales_analytics.db import create_session_factory, init_database
from sales_analytics.db.models import BatchRun, Order, Payment
from sales_analytics.services.batch_service import BatchService
from sales_analytics.services.business_date_service import BusinessDateService
from sales_analytics.services.csv_export_service import CsvExportService
from sales_analytics.services.drive_upload_service import DriveUploadService
from sales_analytics.services.ingestion_service import IngestionService
from sales_analytics.services.normalization_service import NormalizationService


class SparseTossPlaceClient(MockTossPlaceClient):
    def __init__(self, first_date: date):
        self.first_date = first_date

    def fetch_orders(self, merchant, business_date, start_at, end_at):
        if start_at.date() <= self.first_date <= end_at.date():
            return self._page(merchant, self.first_date, start_at)
        if business_date >= self.first_date:
            return self._page(merchant, business_date, start_at)
        return [
            {
                "endpoint": "/mock/orders",
                "request_params": {"from": start_at.isoformat(), "to": end_at.isoformat()},
                "response_body": {"orders": [], "page": 1, "size": 0, "hasNext": False},
                "http_status": 200,
                "x_toss_event_id": f"mock-empty-{business_date}",
                "orders": [],
            }
        ]

    def _page(self, merchant, business_date, start_at):
        orders = self._build_orders(merchant, business_date, start_at)
        return [
            {
                "endpoint": "/mock/orders",
                "request_params": {"from": start_at.isoformat()},
                "response_body": {"orders": orders, "page": 1, "size": len(orders), "hasNext": False},
                "http_status": 200,
                "x_toss_event_id": f"mock-{business_date}",
                "orders": orders,
            }
        ]


class PipelineTest(unittest.TestCase):
    def test_mock_pipeline_is_idempotent_and_skips_daily_reports_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            database_url = f"sqlite:///{root / 'sales.db'}"
            init_database(database_url)
            factory = create_session_factory(database_url)
            merchant = MerchantConfig(merchant_id=1, merchant_name="Demo Store")
            service = BatchService(
                session_factory=factory,
                ingestion=IngestionService(MockTossPlaceClient(), BusinessDateService()),
                normalization=NormalizationService(),
                csv_export=CsvExportService(root / "reports"),
                drive_upload=DriveUploadService(LocalDriveClient(root / "uploads")),
            )

            first = service.run_for_merchant(merchant, date(2026, 6, 17))
            second = service.run_for_merchant(merchant, date(2026, 6, 17))

            with factory() as session:
                order_count = len(session.scalars(select(Order.order_id)).all())
                payment_count = len(session.scalars(select(Payment.payment_id)).all())

            self.assertEqual(first.status, "SUCCESS")
            self.assertEqual(second.status, "SUCCESS")
            self.assertEqual(order_count, first.orders_count)
            self.assertEqual(payment_count, first.payments_count)
            self.assertEqual(first.csv_files_count, 0)
            self.assertEqual(first.uploaded_files_count, 0)
            self.assertFalse((root / "reports" / "merchant_1" / "2026-06-17").exists())

    def test_discovered_bootstrap_starts_from_first_transaction_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            database_url = f"sqlite:///{root / 'sales.db'}"
            init_database(database_url)
            factory = create_session_factory(database_url)
            merchant = MerchantConfig(merchant_id=1, merchant_name="Demo Store")
            first_date = date.today() - timedelta(days=2)
            service = BatchService(
                session_factory=factory,
                ingestion=IngestionService(SparseTossPlaceClient(first_date), BusinessDateService()),
                normalization=NormalizationService(),
                csv_export=CsvExportService(root / "reports"),
                drive_upload=DriveUploadService(LocalDriveClient(root / "uploads")),
            )

            _run_discovered_bootstrap_backfill(service, [merchant], lookback_years=1, end_offset_days=1)

            with factory() as session:
                runs = session.scalars(select(BatchRun).where(BatchRun.status == "SUCCESS")).all()
                dates = sorted(run.business_date for run in runs)

            self.assertEqual(dates[0], first_date)
            self.assertEqual(dates[-1], date.today() - timedelta(days=1))
            self.assertEqual(len(list((root / "uploads" / "merchant_1_Demo_Store").glob("**/*daily_*.csv"))), 8)
            self.assertEqual(len(list((root / "uploads" / "merchant_1_Demo_Store").glob("**/*weekly_*.csv"))), 4)
            self.assertEqual(len(list((root / "uploads" / "merchant_1_Demo_Store").glob("**/*monthly_*.csv"))), 4)
            self.assertEqual(len(list((root / "uploads" / "merchant_1_Demo_Store").glob("**/*yearly_*.csv"))), 4)


if __name__ == "__main__":
    unittest.main()
