from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from sales_analytics.clients.google_drive_client import build_drive_client, create_oauth_token
from sales_analytics.clients.toss_place_client import TossPlaceApiError
from sales_analytics.clients.toss_place_client import build_toss_client
from sales_analytics.config import MerchantConfig, load_settings
from sales_analytics.db.models import BatchRun
from sales_analytics.db import create_session_factory, init_database
from sales_analytics.services.batch_service import BatchService
from sales_analytics.services.bootstrap_discovery_service import BootstrapDiscoveryService, discovery_start_date
from sales_analytics.services.business_date_service import BusinessDateService
from sales_analytics.services.csv_export_service import CsvExportService
from sales_analytics.services.drive_upload_service import DriveUploadService
from sales_analytics.services.ingestion_service import IngestionService
from sales_analytics.services.normalization_service import NormalizationService
from sales_analytics.services.periodic_report_service import PeriodicReportService
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

    check_parser = subparsers.add_parser("check-toss-api", help="Call Toss API without DB writes or Drive uploads")
    check_parser.add_argument("--business-date", default=None, help="Business date in YYYY-MM-DD. Defaults to yesterday.")
    check_parser.add_argument("--merchant-id", type=int, default=None, help="Only check one merchant.")

    serve_parser = subparsers.add_parser("serve", help="Run the daily scheduler server")
    serve_parser.add_argument("--merchant-id", type=int, default=None, help="Only schedule one merchant.")
    serve_parser.add_argument("--close-delay-minutes", type=int, default=None, help="Delay after business close before running.")
    serve_parser.add_argument("--lookback-days", type=int, default=None, help="Past business dates to check for missed runs.")
    serve_parser.add_argument(
        "--discovery-lookback-years",
        type=int,
        default=None,
        help="Search this many recent years for the first transaction date before startup backfill.",
    )
    serve_parser.add_argument(
        "--skip-bootstrap",
        action="store_true",
        help="Skip startup backfill even when BOOTSTRAP_BACKFILL_ON_START=true.",
    )
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
        merchants = _select_merchants(settings.merchants, args.merchant_id)
        current = start
        while current <= end:
            _run_for_date(service, merchants, current)
            current += timedelta(days=1)
        if settings.aggregate_reports_enabled:
            _export_closing_period_reports_for_range(service, merchants, start, end, include_weekly=False)
        return

    if command == "check-toss-api":
        business_date = date.fromisoformat(args.business_date) if args.business_date else date.today() - timedelta(days=1)
        _check_toss_api(settings, _select_merchants(settings.merchants, args.merchant_id), business_date)
        return

    if command == "serve":
        merchants = _select_merchants(settings.merchants, args.merchant_id)
        discovery_lookback_years = (
            args.discovery_lookback_years
            if args.discovery_lookback_years is not None
            else settings.bootstrap_discovery_lookback_years
        )
        if settings.bootstrap_backfill_on_start and not args.skip_bootstrap:
            if settings.bootstrap_discovery_enabled and discovery_lookback_years > 0:
                _run_discovered_bootstrap_backfill(
                    service,
                    merchants,
                    lookback_years=discovery_lookback_years,
                    end_offset_days=settings.bootstrap_backfill_end_offset_days,
                    aggregate_reports_enabled=settings.aggregate_reports_enabled,
                )
        scheduler = DailyBatchScheduler(
            batch_service=service,
            merchants=merchants,
            config=SchedulerConfig(
                close_delay_minutes=args.close_delay_minutes or settings.scheduler_close_delay_minutes,
                lookback_days=args.lookback_days if args.lookback_days is not None else settings.scheduler_lookback_days,
            ),
            on_success=(
                lambda merchant, result: _export_scheduled_reports_after_run(service, merchant, result)
                if settings.aggregate_reports_enabled
                else None
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
    for merchant, result in _run_for_date(service, _select_merchants(settings.merchants, args.merchant_id), business_date):
        if settings.aggregate_reports_enabled:
            _export_daily_report_for_result(service, merchant, result)


def _build_batch_service(settings) -> BatchService:
    session_factory = create_session_factory(settings.database_url)
    business_dates = BusinessDateService()
    toss_client = build_toss_client(settings)
    drive_client = build_drive_client(settings)
    return BatchService(
        session_factory=session_factory,
        ingestion=IngestionService(toss_client, business_dates),
        normalization=NormalizationService(),
        csv_export=CsvExportService(settings.output_dir),
        drive_upload=DriveUploadService(drive_client),
    )


def _select_merchants(merchants: tuple[MerchantConfig, ...], merchant_id: int | None) -> list[MerchantConfig]:
    selected = [merchant for merchant in merchants if merchant.is_active and (merchant_id is None or merchant.merchant_id == merchant_id)]
    if not selected:
        raise SystemExit(f"No active merchant matched merchant_id={merchant_id}")
    return selected


def _run_for_date(service: BatchService, merchants: list[MerchantConfig], business_date: date) -> list[tuple[MerchantConfig, object]]:
    results = []
    for merchant in merchants:
        result = service.run_for_merchant(merchant, business_date)
        results.append((merchant, result))
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
    return results


def _run_discovered_bootstrap_backfill(
    service: BatchService,
    merchants: list[MerchantConfig],
    lookback_years: int,
    end_offset_days: int,
    aggregate_reports_enabled: bool = True,
) -> None:
    discovery = BootstrapDiscoveryService(service.ingestion.client, service.ingestion.business_dates)
    for merchant in merchants:
        today = datetime.now(ZoneInfo(merchant.timezone)).date()
        end = today - timedelta(days=end_offset_days)
        start = discovery_start_date(end, lookback_years)
        latest_successful_date = _latest_successful_business_date(service, merchant.merchant_id)
        if latest_successful_date is not None:
            if latest_successful_date >= end:
                print(
                    "bootstrap_backfill_already_current "
                    f"merchant_id={merchant.merchant_id} "
                    f"latest_successful_business_date={latest_successful_date} "
                    f"to_date={end}",
                    flush=True,
                )
                continue
            resume_start = max(start, latest_successful_date + timedelta(days=1))
            print(
                "bootstrap_backfill_resume "
                f"merchant_id={merchant.merchant_id} "
                f"latest_successful_business_date={latest_successful_date} "
                f"from_date={resume_start} "
                f"to_date={end}",
                flush=True,
            )
            _run_bootstrap_backfill_for_range(service, merchant, resume_start, end, aggregate_reports_enabled)
            continue
        print(
            "bootstrap_discovery_started "
            f"merchant_id={merchant.merchant_id} "
            f"from_date={start} "
            f"to_date={end} "
            f"lookback_years={lookback_years}",
            flush=True,
        )
        first_date = discovery.find_first_transaction_date(merchant, start, end)
        if first_date is None:
            print(
                "bootstrap_discovery_no_transactions "
                f"merchant_id={merchant.merchant_id} "
                f"from_date={start} "
                f"to_date={end}",
                flush=True,
            )
            continue
        print(
            "bootstrap_discovery_found "
            f"merchant_id={merchant.merchant_id} "
            f"first_business_date={first_date}",
            flush=True,
        )
        _run_bootstrap_backfill_for_range(service, merchant, first_date, end, aggregate_reports_enabled)


def _run_bootstrap_backfill_for_range(
    service: BatchService,
    merchant: MerchantConfig,
    start: date,
    end: date,
    aggregate_reports_enabled: bool,
) -> None:
    print(
        "bootstrap_backfill_started "
        f"merchant_id={merchant.merchant_id} "
        f"from_date={start} "
        f"to_date={end} "
        f"days={(end - start).days + 1}",
        flush=True,
    )
    completed = 0
    skipped = 0
    failed = 0
    for month_start in _month_starts(start, end):
        month_end = min(_next_month(month_start) - timedelta(days=1), end)
        month_start = max(month_start, start)
        month_completed, month_skipped, month_failed = _run_bootstrap_month_or_daily(
            service, merchant, month_start, month_end
        )
        completed += month_completed
        skipped += month_skipped
        failed += month_failed
    print(
        "bootstrap_backfill_finished "
        f"merchant_id={merchant.merchant_id} "
        f"completed={completed} "
        f"skipped={skipped} "
        f"failed={failed}",
        flush=True,
    )
    if aggregate_reports_enabled and completed > 0:
        _export_closing_period_reports_for_range(service, [merchant], start, end, include_weekly=False)


def _run_bootstrap_month_or_daily(
    service: BatchService,
    merchant: MerchantConfig,
    start: date,
    end: date,
) -> tuple[int, int, int]:
    pending_dates = [current for current in _date_range(start, end) if not service.has_successful_run(merchant.merchant_id, current)]
    skipped = (end - start).days + 1 - len(pending_dates)
    if not pending_dates:
        return 0, skipped, 0
    try:
        return _run_bootstrap_month_range(service, merchant, min(pending_dates), max(pending_dates), pending_dates, skipped)
    except TossPlaceApiError as exc:
        print(
            "bootstrap_backfill_month_fallback "
            f"merchant_id={merchant.merchant_id} "
            f"from_date={start} "
            f"to_date={end} "
            f"error={exc}",
            flush=True,
        )
        return _run_bootstrap_daily_range(service, merchant, start, end)


def _run_bootstrap_month_range(
    service: BatchService,
    merchant: MerchantConfig,
    start: date,
    end: date,
    pending_dates: list[date],
    skipped: int,
) -> tuple[int, int, int]:
    start_at, _ = service.ingestion.business_dates.calculate_range(merchant, start)
    _, end_at = service.ingestion.business_dates.calculate_range(merchant, end)
    with service.session_factory() as session:
        service._upsert_merchant(session, merchant)
        grouped_orders = service.ingestion.ingest_range(session, merchant, start, end, start_at, end_at)
        completed = 0
        for business_date in pending_dates:
            run = BatchRun(merchant_id=merchant.merchant_id, business_date=business_date)
            session.add(run)
            session.flush()
            try:
                service.normalization.normalize_orders(
                    session,
                    merchant.merchant_id,
                    business_date,
                    grouped_orders.get(business_date, []),
                )
                orders_count, payments_count = service._count_normalized_rows(session, merchant.merchant_id, business_date)
                run.status = "SUCCESS"
                run.orders_count = orders_count
                run.payments_count = payments_count
                run.csv_files_count = 0
                run.drive_upload_status = "SKIPPED"
                run.finished_at = datetime.now(ZoneInfo("UTC")).replace(tzinfo=None)
                completed += 1
                print(
                    "bootstrap_backfill_success "
                    f"run_id={run.run_id} "
                    f"merchant_id={merchant.merchant_id} "
                    f"business_date={business_date} "
                    f"orders={orders_count} "
                    f"payments={payments_count} "
                    f"csv_files=0 "
                    f"uploaded_files=0",
                    flush=True,
                )
            except Exception as exc:
                run.status = "FAILED"
                run.error_message = str(exc)
                run.finished_at = datetime.now(ZoneInfo("UTC")).replace(tzinfo=None)
                raise
        session.commit()
    return completed, skipped, 0


def _run_bootstrap_daily_range(service: BatchService, merchant: MerchantConfig, start: date, end: date) -> tuple[int, int, int]:
    completed = 0
    skipped = 0
    failed = 0
    for current in _date_range(start, end):
        if service.has_successful_run(merchant.merchant_id, current):
            skipped += 1
            continue
        try:
            result = service.run_for_merchant(merchant, current)
        except Exception as exc:
            failed += 1
            print(
                "bootstrap_backfill_failed "
                f"merchant_id={merchant.merchant_id} "
                f"business_date={current} "
                f"error={exc}",
                flush=True,
            )
            continue
        completed += 1
        print(
            "bootstrap_backfill_success "
            f"run_id={result.run_id} "
            f"merchant_id={result.merchant_id} "
            f"business_date={result.business_date} "
            f"orders={result.orders_count} "
            f"payments={result.payments_count} "
            f"csv_files={result.csv_files_count} "
            f"uploaded_files={result.uploaded_files_count}",
            flush=True,
        )
    return completed, skipped, failed


def _export_daily_report_for_result(service: BatchService, merchant: MerchantConfig, result) -> None:
    _export_daily_report_for_date(service, merchant, result.business_date, run_id=result.run_id)


def _export_daily_report_for_date(service: BatchService, merchant: MerchantConfig, business_date: date, run_id: str | None = None) -> None:
    run_id = run_id or _latest_successful_run_id(service, merchant.merchant_id, business_date, business_date)
    if run_id is None:
        return
    with service.session_factory() as session:
        reports = PeriodicReportService().build_daily_reports(session, merchant.merchant_id, business_date)
        exported = service.csv_export.export_period(merchant, "daily", business_date, reports.frames)
        uploaded = service.drive_upload.upload_period_files(session, run_id, merchant, business_date, "daily", exported)
        session.commit()
    print(
        "period_report_uploaded "
        f"merchant_id={merchant.merchant_id} "
        f"period_type=daily "
        f"period={business_date} "
        f"csv_files={len(exported)} "
        f"uploaded_files={uploaded}",
        flush=True,
    )


def _export_scheduled_reports_after_run(service: BatchService, merchant: MerchantConfig, result) -> None:
    business_date = result.business_date
    _export_daily_report_for_result(service, merchant, result)
    if _is_sunday(business_date):
        _export_weekly_report_for_date(service, merchant, business_date)
        _export_monthly_report_for_date(service, merchant, business_date)
    if _is_last_day_of_month(business_date):
        _export_yearly_report_for_date(service, merchant, business_date)


def _export_closing_period_reports_for_range(
    service: BatchService,
    merchants: list[MerchantConfig],
    start: date,
    end: date,
    include_weekly: bool = True,
) -> None:
    for merchant in merchants:
        if include_weekly:
            for week_start in _week_starts(start, end):
                week_end = min(week_start + timedelta(days=6), end)
                if _has_successful_run_in_period(service, merchant.merchant_id, week_start, week_end):
                    _export_weekly_report(service, merchant, week_start)

        for month_start in _month_starts(start, end):
            month_end = min(_next_month(month_start) - timedelta(days=1), end)
            if not _has_successful_run_in_period(service, merchant.merchant_id, month_start, month_end):
                continue
            _export_monthly_report(service, merchant, month_start)

        for year_start in _year_starts(start, end):
            year_end = min(date(year_start.year, 12, 31), end)
            if not _has_successful_run_in_period(service, merchant.merchant_id, year_start, year_end):
                continue
            _export_yearly_report(service, merchant, year_start)


def _export_weekly_report_for_date(service: BatchService, merchant: MerchantConfig, business_date: date) -> None:
    _export_weekly_report(service, merchant, _week_start(business_date))


def _export_monthly_report_for_date(service: BatchService, merchant: MerchantConfig, business_date: date) -> None:
    _export_monthly_report(service, merchant, date(business_date.year, business_date.month, 1))


def _export_yearly_report_for_date(service: BatchService, merchant: MerchantConfig, business_date: date) -> None:
    _export_yearly_report(service, merchant, date(business_date.year, 1, 1))


def _export_weekly_report(service: BatchService, merchant: MerchantConfig, week_start: date) -> None:
    week_end = week_start + timedelta(days=6)
    run_id = _latest_successful_run_id(service, merchant.merchant_id, week_start, week_end)
    if run_id is None:
        return
    with service.session_factory() as session:
        reports = PeriodicReportService().build_weekly_reports(session, merchant.merchant_id, week_start)
        exported = service.csv_export.export_period(merchant, "weekly", week_start, reports.frames)
        uploaded = service.drive_upload.upload_period_files(session, run_id, merchant, week_start, "weekly", exported)
        session.commit()
    iso_year, iso_week, _ = week_start.isocalendar()
    _print_period_upload(merchant, "weekly", f"{iso_year:04d}-W{iso_week:02d}", exported, uploaded)


def _export_monthly_report(service: BatchService, merchant: MerchantConfig, month_start: date) -> None:
    month_end = _next_month(month_start) - timedelta(days=1)
    run_id = _latest_successful_run_id(service, merchant.merchant_id, month_start, month_end)
    if run_id is None:
        return
    with service.session_factory() as session:
        reports = PeriodicReportService().build_monthly_reports(session, merchant.merchant_id, month_start.year, month_start.month)
        exported = service.csv_export.export_period(merchant, "monthly", month_start, reports.frames)
        uploaded = service.drive_upload.upload_period_files(session, run_id, merchant, month_start, "monthly", exported)
        session.commit()
    _print_period_upload(merchant, "monthly", f"{month_start:%Y-%m}", exported, uploaded)


def _export_yearly_report(service: BatchService, merchant: MerchantConfig, year_start: date) -> None:
    year_end = date(year_start.year, 12, 31)
    run_id = _latest_successful_run_id(service, merchant.merchant_id, year_start, year_end)
    if run_id is None:
        return
    with service.session_factory() as session:
        reports = PeriodicReportService().build_yearly_reports(session, merchant.merchant_id, year_start.year)
        exported = service.csv_export.export_period(merchant, "yearly", year_start, reports.frames)
        uploaded = service.drive_upload.upload_period_files(session, run_id, merchant, year_start, "yearly", exported)
        session.commit()
    _print_period_upload(merchant, "yearly", f"{year_start:%Y}", exported, uploaded)


def _print_period_upload(merchant: MerchantConfig, period_type: str, period: str, exported, uploaded: int) -> None:
    print(
        "period_report_uploaded "
        f"merchant_id={merchant.merchant_id} "
        f"period_type={period_type} "
        f"period={period} "
        f"csv_files={len(exported)} "
        f"uploaded_files={uploaded}",
        flush=True,
    )


def _has_successful_run_in_period(service: BatchService, merchant_id: int, start: date, end: date) -> bool:
    with service.session_factory() as session:
        count = session.scalar(
            select(func.count(BatchRun.run_id)).where(
                BatchRun.merchant_id == merchant_id,
                BatchRun.business_date >= start,
                BatchRun.business_date <= end,
                BatchRun.status == "SUCCESS",
            )
        )
        return bool(count)


def _latest_successful_run_id(service: BatchService, merchant_id: int, start: date, end: date) -> str | None:
    with service.session_factory() as session:
        return session.scalar(
            select(BatchRun.run_id)
            .where(
                BatchRun.merchant_id == merchant_id,
                BatchRun.business_date >= start,
                BatchRun.business_date <= end,
                BatchRun.status == "SUCCESS",
            )
            .order_by(BatchRun.finished_at.desc(), BatchRun.started_at.desc())
            .limit(1)
        )


def _latest_successful_business_date(service: BatchService, merchant_id: int) -> date | None:
    with service.session_factory() as session:
        return session.scalar(
            select(func.max(BatchRun.business_date)).where(
                BatchRun.merchant_id == merchant_id,
                BatchRun.status == "SUCCESS",
            )
        )


def _month_starts(start: date, end: date) -> list[date]:
    current = date(start.year, start.month, 1)
    result = []
    while current <= end:
        result.append(current)
        current = _next_month(current)
    return result


def _week_starts(start: date, end: date) -> list[date]:
    current = _week_start(start)
    result = []
    while current <= end:
        result.append(current)
        current += timedelta(days=7)
    return result


def _week_start(value: date) -> date:
    return value - timedelta(days=value.weekday())


def _date_range(start: date, end: date) -> list[date]:
    current = start
    result = []
    while current <= end:
        result.append(current)
        current += timedelta(days=1)
    return result


def _year_starts(start: date, end: date) -> list[date]:
    return [date(year, 1, 1) for year in range(start.year, end.year + 1)]


def _next_month(value: date) -> date:
    if value.month == 12:
        return date(value.year + 1, 1, 1)
    return date(value.year, value.month + 1, 1)


def _is_sunday(value: date) -> bool:
    return value.weekday() == 6


def _is_last_day_of_month(value: date) -> bool:
    return _next_month(date(value.year, value.month, 1)) - timedelta(days=1) == value


def _check_toss_api(settings, merchants: list[MerchantConfig], business_date: date) -> None:
    client = build_toss_client(settings)
    business_dates = BusinessDateService()
    for merchant in merchants:
        merchant_payload = client.fetch_merchant(merchant)
        start_at, end_at = business_dates.calculate_range(merchant, business_date)
        pages = client.fetch_orders(merchant, business_date, start_at, end_at)
        orders_count = sum(len(page["orders"]) for page in pages)
        first_page = pages[0] if pages else {}
        print(
            " ".join(
                [
                    f"merchant_id={merchant.merchant_id}",
                    f"merchant_name={merchant_payload.get('name') or merchant_payload.get('displayName') or merchant.merchant_name}",
                    f"business_date={business_date}",
                    f"orders={orders_count}",
                    f"pages={len(pages)}",
                    f"http_status={first_page.get('http_status')}",
                    f"resultType={(first_page.get('response_body') or {}).get('resultType')}",
                    f"x_toss_event_id={first_page.get('x_toss_event_id')}",
                ]
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
