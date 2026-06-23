from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from sales_analytics.cli import _run_discovered_bootstrap_backfill
from sales_analytics.clients.google_drive_client import LocalDriveClient
from sales_analytics.clients.toss_place_client import MockTossPlaceClient
from sales_analytics.config import MerchantConfig
from sales_analytics.db import create_session_factory, init_database
from sales_analytics.db.models import BatchRun, Merchant, Order, OrderLineItem, Payment
from sales_analytics.services.batch_service import BatchService
from sales_analytics.services.bootstrap_discovery_service import discovery_start_date
from sales_analytics.services.business_date_service import BusinessDateService
from sales_analytics.services.csv_export_service import CsvExportService
from sales_analytics.services.drive_upload_service import DriveUploadService
from sales_analytics.services.ingestion_service import IngestionService
from sales_analytics.services.normalization_service import NormalizationService
from sales_analytics.services.periodic_report_service import PeriodicReportService


class SparseTossPlaceClient(MockTossPlaceClient):
    def __init__(self, first_date: date):
        self.first_date = first_date
        self.fetch_orders_calls = 0

    def fetch_orders(self, merchant, business_date, start_at, end_at):
        self.fetch_orders_calls += 1
        if start_at.date() != business_date or end_at.date() != business_date:
            raise AssertionError("bootstrap discovery must probe one business day per API call")
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
            self.assertEqual(len(list((root / "uploads" / "merchant_1_Demo_Store").glob("**/*daily_*.csv"))), 0)
            self.assertEqual(len(list((root / "uploads" / "merchant_1_Demo_Store").glob("**/*weekly_*.csv"))), 0)
            self.assertEqual(len(list((root / "uploads" / "merchant_1_Demo_Store").glob("**/*monthly_*.csv"))), 4)
            self.assertEqual(len(list((root / "uploads" / "merchant_1_Demo_Store").glob("**/*yearly_*.csv"))), 4)

    def test_discovered_bootstrap_skips_api_and_uploads_when_db_is_current(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            database_url = f"sqlite:///{root / 'sales.db'}"
            init_database(database_url)
            factory = create_session_factory(database_url)
            merchant = MerchantConfig(merchant_id=1, merchant_name="Demo Store")
            first_date = date.today() - timedelta(days=2)
            client = SparseTossPlaceClient(first_date)
            service = BatchService(
                session_factory=factory,
                ingestion=IngestionService(client, BusinessDateService()),
                normalization=NormalizationService(),
                csv_export=CsvExportService(root / "reports"),
                drive_upload=DriveUploadService(LocalDriveClient(root / "uploads")),
            )

            _run_discovered_bootstrap_backfill(service, [merchant], lookback_years=1, end_offset_days=1)
            calls_after_first_run = client.fetch_orders_calls
            uploads_after_first_run = len(list((root / "uploads" / "merchant_1_Demo_Store").glob("**/*.csv")))

            _run_discovered_bootstrap_backfill(service, [merchant], lookback_years=1, end_offset_days=1)

            self.assertEqual(client.fetch_orders_calls, calls_after_first_run)
            self.assertEqual(len(list((root / "uploads" / "merchant_1_Demo_Store").glob("**/*.csv"))), uploads_after_first_run)

    def test_discovery_start_date_respects_toss_api_minimum(self) -> None:
        self.assertEqual(discovery_start_date(date(2026, 6, 20), 5), date(2022, 1, 1))
        self.assertEqual(discovery_start_date(date(2026, 6, 20), 1), date(2025, 6, 20))

    def test_periodic_reports_render_timestamps_in_merchant_timezone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            database_url = f"sqlite:///{root / 'sales.db'}"
            init_database(database_url)
            factory = create_session_factory(database_url)
            with factory() as session:
                session.add(
                    Merchant(
                        merchant_id=1,
                        merchant_name="Demo Store",
                        timezone="Asia/Seoul",
                    )
                )
                session.add(
                    Order(
                        order_id="order-1",
                        merchant_id=1,
                        business_date=date(2025, 7, 1),
                        order_state="COMPLETED",
                        created_at=datetime(2025, 7, 1, 0, 5, 16),
                        completed_at=datetime(2025, 7, 1, 0, 5, 17),
                        total_amount=5700,
                    )
                )
                session.add(
                    OrderLineItem(
                        order_id="order-1",
                        merchant_id=1,
                        business_date=date(2025, 7, 1),
                        item_title="Bread",
                        category_title="Bakery",
                        quantity=1,
                        unit_price=5700,
                        line_total_amount=5700,
                    )
                )
                session.add(
                    Payment(
                        payment_id="payment-1",
                        order_id="order-1",
                        merchant_id=1,
                        business_date=date(2025, 7, 1),
                        state="APPROVED",
                        payment_method="CARD",
                        amount=5700,
                        approved_at=datetime(2025, 7, 1, 0, 5, 17),
                        created_at=datetime(2025, 7, 1, 0, 5, 16),
                    )
                )
                session.commit()

                reports = PeriodicReportService().build_monthly_reports(session, 1, 2025, 7)

            order_row = reports.frames["monthly_order_details"].iloc[0]
            payment_row = reports.frames["monthly_all_payments"].iloc[0]
            self.assertEqual(order_row["created_at"], "2025-07-01 09:05:16")
            self.assertEqual(order_row["completed_at"], "2025-07-01 09:05:17")
            self.assertEqual(payment_row["approved_at"], "2025-07-01 09:05:17")


if __name__ == "__main__":
    unittest.main()
