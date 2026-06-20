from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sales_analytics.clients.toss_place_client import TossPlaceClient
from sales_analytics.config import MerchantConfig
from sales_analytics.services.business_date_service import BusinessDateService


@dataclass(frozen=True)
class TransactionProbe:
    orders_count: int
    payments_count: int

    @property
    def has_transactions(self) -> bool:
        return self.orders_count > 0 or self.payments_count > 0


class BootstrapDiscoveryService:
    def __init__(self, client: TossPlaceClient, business_dates: BusinessDateService):
        self.client = client
        self.business_dates = business_dates

    def find_first_transaction_date(self, merchant: MerchantConfig, start: date, end: date) -> date | None:
        current = date(start.year, start.month, 1)
        while current <= end:
            month_start = max(current, start)
            month_end = min(_next_month(current) - timedelta(days=1), end)
            month_probe = self.probe_range(merchant, month_start, month_end)
            print(
                "bootstrap_discovery_month "
                f"merchant_id={merchant.merchant_id} "
                f"month={current:%Y-%m} "
                f"orders={month_probe.orders_count} "
                f"payments={month_probe.payments_count}",
                flush=True,
            )
            if month_probe.has_transactions:
                return self._find_first_day_in_month(merchant, month_start, month_end)
            current = _next_month(current)
        return None

    def probe_range(self, merchant: MerchantConfig, start: date, end: date) -> TransactionProbe:
        start_at, _ = self.business_dates.calculate_range(merchant, start)
        _, end_at = self.business_dates.calculate_range(merchant, end)
        pages = self.client.fetch_orders(merchant, start, start_at, end_at)
        orders = [order for page in pages for order in page["orders"]]
        payments_count = sum(len(order.get("payments") or []) for order in orders)
        return TransactionProbe(orders_count=len(orders), payments_count=payments_count)

    def _find_first_day_in_month(self, merchant: MerchantConfig, start: date, end: date) -> date | None:
        current = start
        while current <= end:
            day_probe = self.probe_range(merchant, current, current)
            print(
                "bootstrap_discovery_day "
                f"merchant_id={merchant.merchant_id} "
                f"business_date={current} "
                f"orders={day_probe.orders_count} "
                f"payments={day_probe.payments_count}",
                flush=True,
            )
            if day_probe.has_transactions:
                return current
            current += timedelta(days=1)
        return None


def discovery_start_date(end: date, lookback_years: int) -> date:
    try:
        return end.replace(year=end.year - lookback_years)
    except ValueError:
        return end.replace(year=end.year - lookback_years, day=28)


def _next_month(value: date) -> date:
    if value.month == 12:
        return date(value.year + 1, 1, 1)
    return date(value.year, value.month + 1, 1)
