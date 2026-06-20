from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime, time as clock_time, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

from sales_analytics.config import MerchantConfig
from sales_analytics.services.batch_service import BatchResult, BatchService


@dataclass(frozen=True)
class SchedulerConfig:
    close_delay_minutes: int = 60
    lookback_days: int = 2


class DailyBatchScheduler:
    def __init__(
        self,
        batch_service: BatchService,
        merchants: list[MerchantConfig],
        config: SchedulerConfig,
        on_success: Callable[[MerchantConfig, BatchResult], None] | None = None,
    ):
        self.batch_service = batch_service
        self.merchants = merchants
        self.config = config
        self.on_success = on_success

    def run_forever(self) -> None:
        print(
            "scheduler_started "
            f"merchants={len(self.merchants)} "
            f"close_delay_minutes={self.config.close_delay_minutes} "
            f"lookback_days={self.config.lookback_days}",
            flush=True,
        )
        while True:
            self.run_due_once()
            sleep_seconds, next_run_at = self._seconds_until_next_run()
            print(
                "scheduler_sleep "
                f"next_run_at={next_run_at.isoformat()} "
                f"sleep_seconds={sleep_seconds}",
                flush=True,
            )
            time.sleep(sleep_seconds)

    def run_due_once(self) -> list[BatchResult]:
        results: list[BatchResult] = []
        for merchant in self.merchants:
            for business_date in self._candidate_business_dates(merchant):
                if not self._is_due(merchant, business_date):
                    continue
                if self.batch_service.has_successful_run(merchant.merchant_id, business_date):
                    continue
                print(
                    "scheduler_run_due "
                    f"merchant_id={merchant.merchant_id} "
                    f"business_date={business_date}",
                    flush=True,
                )
                try:
                    result = self.batch_service.run_for_merchant(merchant, business_date)
                except Exception as exc:
                    print(
                        "scheduler_run_failed "
                        f"merchant_id={merchant.merchant_id} "
                        f"business_date={business_date} "
                        f"error={exc}",
                        flush=True,
                    )
                    continue
                results.append(result)
                print(
                    "scheduler_run_success "
                    f"run_id={result.run_id} "
                    f"merchant_id={result.merchant_id} "
                    f"business_date={result.business_date} "
                    f"orders={result.orders_count} "
                    f"payments={result.payments_count} "
                    f"csv_files={result.csv_files_count} "
                    f"uploaded_files={result.uploaded_files_count}",
                    flush=True,
                )
                if self.on_success is not None:
                    try:
                        self.on_success(merchant, result)
                    except Exception as exc:
                        print(
                            "scheduler_post_success_failed "
                            f"merchant_id={merchant.merchant_id} "
                            f"business_date={business_date} "
                            f"error={exc}",
                            flush=True,
                        )
        return results

    def _candidate_business_dates(self, merchant: MerchantConfig) -> list[date]:
        now = datetime.now(ZoneInfo(merchant.timezone))
        today = now.date()
        return [today - timedelta(days=offset) for offset in range(self.config.lookback_days + 1)]

    def _is_due(self, merchant: MerchantConfig, business_date: date) -> bool:
        now = datetime.now(ZoneInfo(merchant.timezone))
        return now >= self._scheduled_run_at(merchant, business_date)

    def _scheduled_run_at(self, merchant: MerchantConfig, business_date: date) -> datetime:
        tz = ZoneInfo(merchant.timezone)
        open_time = clock_time.fromisoformat(merchant.business_open_time)
        close_time = clock_time.fromisoformat(merchant.business_close_time)
        close_day = business_date + timedelta(days=1) if close_time <= open_time else business_date
        close_at = datetime.combine(close_day, close_time, tzinfo=tz)
        return close_at + timedelta(minutes=self.config.close_delay_minutes)

    def _seconds_until_next_run(self) -> tuple[int, datetime]:
        candidates: list[datetime] = []
        retry_needed = False
        for merchant in self.merchants:
            now = datetime.now(ZoneInfo(merchant.timezone))
            today = now.date()
            for offset in range(self.config.lookback_days, -2, -1):
                business_date = today - timedelta(days=offset)
                scheduled_at = self._scheduled_run_at(merchant, business_date)
                if self.batch_service.has_successful_run(merchant.merchant_id, business_date):
                    continue
                if scheduled_at <= now:
                    retry_needed = True
                    continue
                candidates.append(scheduled_at)
        if retry_needed:
            retry_at = datetime.now().astimezone() + timedelta(hours=1)
            return 3600, retry_at
        if not candidates:
            fallback = datetime.now().astimezone() + timedelta(hours=1)
            return 3600, fallback
        next_run_at = min(candidates)
        now_for_next = datetime.now(next_run_at.tzinfo)
        return max(1, int((next_run_at - now_for_next).total_seconds())), next_run_at
