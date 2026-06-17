from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sales_analytics.config import MerchantConfig


class BusinessDateService:
    def calculate_range(
        self,
        merchant: MerchantConfig,
        business_date: date,
        close_buffer_minutes: int = 60,
    ) -> tuple[datetime, datetime]:
        tz = ZoneInfo(merchant.timezone)
        open_time = time.fromisoformat(merchant.business_open_time)
        close_time = time.fromisoformat(merchant.business_close_time)
        start = datetime.combine(business_date, open_time, tzinfo=tz)
        close_day = business_date + timedelta(days=1) if close_time <= open_time else business_date
        end = datetime.combine(close_day, close_time, tzinfo=tz) + timedelta(minutes=close_buffer_minutes)
        return start, end
