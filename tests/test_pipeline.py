from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date
from pathlib import Path

from sqlalchemy import select

from sales_analytics.clients.google_drive_client import LocalDriveClient
from sales_analytics.clients.toss_place_client import MockTossPlaceClient
from sales_analytics.config import MerchantConfig
from sales_analytics.db import create_session_factory, init_database
from sales_analytics.db.models import Order, Payment
from sales_analytics.services.analytics_service import AnalyticsService
from sales_analytics.services.batch_service import BatchService
from sales_analytics.services.business_date_service import BusinessDateService
from sales_analytics.services.csv_export_service import CsvExportService
from sales_analytics.services.drive_upload_service import DriveUploadService
from sales_analytics.services.ingestion_service import IngestionService
from sales_analytics.services.normalization_service import NormalizationService


class PipelineTest(unittest.TestCase):
    def test_mock_pipeline_is_idempotent_and_exports_reports(self) -> None:
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
                analytics=AnalyticsService(),
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
            self.assertGreaterEqual(first.csv_files_count, 8)
            self.assertTrue((root / "reports" / "merchant_1" / "2026-06-17").exists())


if __name__ == "__main__":
    unittest.main()
