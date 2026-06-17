from __future__ import annotations

import argparse
from datetime import date, timedelta

from sales_analytics.clients.google_drive_client import build_drive_client, create_oauth_token
from sales_analytics.clients.toss_place_client import build_toss_client
from sales_analytics.config import MerchantConfig, load_settings
from sales_analytics.db import create_session_factory, init_database
from sales_analytics.services.analytics_service import AnalyticsService
from sales_analytics.services.batch_service import BatchService
from sales_analytics.services.business_date_service import BusinessDateService
from sales_analytics.services.csv_export_service import CsvExportService
from sales_analytics.services.drive_upload_service import DriveUploadService
from sales_analytics.services.ingestion_service import IngestionService
from sales_analytics.services.normalization_service import NormalizationService
from sales_analytics.services.scheduler_service import DailyBatchScheduler, SchedulerConfig


def main() -> None:
    parser = argparse.ArgumentParser(description="Daily POS sales analytics pipeline")
    subparsers = parser.add_subparsers(dest="command", required=False)

    run_parser = subparsers.add_parser("run", help="Run the pipeline for one business date")
    run_parser.add_argument("--business-date", default=None, help="Business date in YYYY-MM-DD. Defaults to yesterday.")
    run_parser.add_argument("--merchant-id", type=int, default=None, help="Only run one merchant.")

    backfill_parser = subparsers.add_parser("backfill", help="Run the pipeline for a date range")
    backfill_parser.add_argument("--from-date", required=True, help="Start date in YYYY-MM-DD")
    backfill_parser.add_argument("--to-date", required=True, help="End date in YYYY-MM-DD")
    backfill_parser.add_argument("--merchant-id", type=int, default=None, help="Only run one merchant.")

    serve_parser = subparsers.add_parser("serve", help="Run the daily scheduler server")
    serve_parser.add_argument("--merchant-id", type=int, default=None, help="Only schedule one merchant.")
    serve_parser.add_argument("--poll-seconds", type=int, default=None, help="Scheduler polling interval.")
    serve_parser.add_argument("--close-delay-minutes", type=int, default=None, help="Delay after business close before running.")
    serve_parser.add_argument("--lookback-days", type=int, default=None, help="Past business dates to check for missed runs.")
    serve_parser.add_argument("--once", action="store_true", help="Run one scheduler tick and exit.")

    subparsers.add_parser("init-db", help="Create database tables")
    auth_parser = subparsers.add_parser("auth-google", help="Authorize a personal Google account and save an OAuth token")
    auth_parser.add_argument("--client-secrets-file", default=None, help="Local OAuth Desktop app client JSON path")
    auth_parser.add_argument("--token-file", default=None, help="OAuth token output path")
    auth_parser.add_argument("--host", default="127.0.0.1", help="OAuth callback host")
    auth_parser.add_argument("--port", type=int, default=0, help="OAuth callback port. Defaults to a random free port.")
    args = parser.parse_args()
    command = args.command or "run"

    settings = load_settings()

    if command == "auth-google":
        client_secrets_file = args.client_secrets_file or settings.google_oauth_client_secrets_file
        token_file = args.token_file or settings.google_oauth_token_file
        try:
            token_path = create_oauth_token(client_secrets_file, token_file, host=args.host, port=args.port)
        except ValueError as exc:
            raise SystemExit(f"Error: {exc}") from exc
        print(f"Google OAuth token saved: {token_path}")
        return

    init_database(settings.database_url)
    service = _build_batch_service(settings)

    if command == "init-db":
        print("Database initialized")
        return

    if command == "backfill":
        start = date.fromisoformat(args.from_date)
        end = date.fromisoformat(args.to_date)
        current = start
        while current <= end:
            _run_for_date(service, _select_merchants(settings.merchants, args.merchant_id), current)
            current += timedelta(days=1)
        return

    if command == "serve":
        scheduler = DailyBatchScheduler(
            batch_service=service,
            merchants=_select_merchants(settings.merchants, args.merchant_id),
            config=SchedulerConfig(
                poll_seconds=args.poll_seconds or settings.scheduler_poll_seconds,
                close_delay_minutes=args.close_delay_minutes or settings.scheduler_close_delay_minutes,
                lookback_days=args.lookback_days if args.lookback_days is not None else settings.scheduler_lookback_days,
            ),
        )
        if args.once:
            scheduler.run_due_once()
            return
        try:
            scheduler.run_forever()
        except KeyboardInterrupt:
            print("scheduler_stopped", flush=True)
        return

    business_date = date.fromisoformat(args.business_date) if args.business_date else date.today() - timedelta(days=1)
    _run_for_date(service, _select_merchants(settings.merchants, args.merchant_id), business_date)


def _build_batch_service(settings) -> BatchService:
    session_factory = create_session_factory(settings.database_url)
    business_dates = BusinessDateService()
    toss_client = build_toss_client(settings)
    drive_client = build_drive_client(settings)
    return BatchService(
        session_factory=session_factory,
        ingestion=IngestionService(toss_client, business_dates),
        normalization=NormalizationService(),
        analytics=AnalyticsService(),
        csv_export=CsvExportService(settings.output_dir),
        drive_upload=DriveUploadService(drive_client),
    )


def _select_merchants(merchants: tuple[MerchantConfig, ...], merchant_id: int | None) -> list[MerchantConfig]:
    selected = [merchant for merchant in merchants if merchant.is_active and (merchant_id is None or merchant.merchant_id == merchant_id)]
    if not selected:
        raise SystemExit(f"No active merchant matched merchant_id={merchant_id}")
    return selected


def _run_for_date(service: BatchService, merchants: list[MerchantConfig], business_date: date) -> None:
    for merchant in merchants:
        result = service.run_for_merchant(merchant, business_date)
        print(
            " ".join(
                [
                    f"run_id={result.run_id}",
                    f"merchant_id={result.merchant_id}",
                    f"business_date={result.business_date}",
                    f"status={result.status}",
                    f"orders={result.orders_count}",
                    f"payments={result.payments_count}",
                    f"csv_files={result.csv_files_count}",
                    f"uploaded_files={result.uploaded_files_count}",
                ]
            )
        )


if __name__ == "__main__":
    main()
