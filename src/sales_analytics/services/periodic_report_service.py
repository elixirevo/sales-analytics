from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class PeriodicReports:
    frames: dict[str, pd.DataFrame]


class PeriodicReportService:
    def build_daily_reports(self, session: Session, merchant_id: int, business_date: date) -> PeriodicReports:
        start = business_date
        end = business_date + timedelta(days=1)
        return self._build_reports(session, merchant_id, start, end, business_date.isoformat(), "daily")

    def build_weekly_reports(self, session: Session, merchant_id: int, week_start: date) -> PeriodicReports:
        iso_year, iso_week, _ = week_start.isocalendar()
        start = week_start
        end = week_start + timedelta(days=7)
        return self._build_reports(session, merchant_id, start, end, f"{iso_year:04d}-W{iso_week:02d}", "weekly")

    def build_monthly_reports(self, session: Session, merchant_id: int, year: int, month: int) -> PeriodicReports:
        start = date(year, month, 1)
        end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
        return self._build_reports(session, merchant_id, start, end, f"{year:04d}-{month:02d}", "monthly")

    def build_yearly_reports(self, session: Session, merchant_id: int, year: int) -> PeriodicReports:
        start = date(year, 1, 1)
        end = date(year + 1, 1, 1)
        return self._build_reports(session, merchant_id, start, end, f"{year:04d}", "yearly")

    def _build_reports(
        self,
        session: Session,
        merchant_id: int,
        start: date,
        end_exclusive: date,
        period_label: str,
        period_type: str,
    ) -> PeriodicReports:
        timezone = self._merchant_timezone(session, merchant_id)
        payments = _format_timestamp_columns(self._read_payments(session, merchant_id, start, end_exclusive), timezone)
        details = _format_timestamp_columns(self._read_order_details(session, merchant_id, start, end_exclusive), timezone)
        frames = {
            f"{period_type}_all_payments": self._with_period(payments, period_type, period_label),
            f"{period_type}_order_details": self._with_period(details, period_type, period_label),
            f"{period_type}_item_order_summary": self._item_summary(details, period_type, period_label),
            f"{period_type}_payment_summary": self._payment_summary(payments, period_type, period_label),
        }
        return PeriodicReports(frames=frames)

    def _read_payments(self, session: Session, merchant_id: int, start: date, end_exclusive: date) -> pd.DataFrame:
        return pd.read_sql_query(
            text(
                """
                select *
                from payments
                where merchant_id = :merchant_id
                  and business_date >= :start_date
                  and business_date < :end_date
                order by business_date, approved_at, created_at, payment_id
                """
            ),
            session.connection(),
            params={"merchant_id": merchant_id, "start_date": start, "end_date": end_exclusive},
        )

    def _read_order_details(self, session: Session, merchant_id: int, start: date, end_exclusive: date) -> pd.DataFrame:
        return pd.read_sql_query(
            text(
                """
                select
                    o.business_date,
                    o.order_id,
                    o.order_number,
                    o.order_state,
                    o.source,
                    o.created_at,
                    o.completed_at,
                    o.cancelled_at,
                    o.list_price,
                    o.discount_amount,
                    o.tax_amount,
                    o.supply_amount,
                    o.tax_exempt_amount,
                    o.total_amount,
                    li.item_title,
                    li.item_code,
                    li.category_title,
                    li.dining_option,
                    li.quantity,
                    li.unit_price,
                    li.option_amount,
                    li.line_discount_amount,
                    li.line_total_amount
                from orders o
                left join order_line_items li on li.order_id = o.order_id
                where o.merchant_id = :merchant_id
                  and o.business_date >= :start_date
                  and o.business_date < :end_date
                order by o.business_date, o.created_at, o.order_id, li.item_title
                """
            ),
            session.connection(),
            params={"merchant_id": merchant_id, "start_date": start, "end_date": end_exclusive},
        )

    def _with_period(self, frame: pd.DataFrame, period_type: str, period_label: str) -> pd.DataFrame:
        result = frame.copy()
        result.insert(0, "period", period_label)
        result.insert(0, "period_type", period_type)
        return result

    def _item_summary(self, details: pd.DataFrame, period_type: str, period_label: str) -> pd.DataFrame:
        columns = [
            "period_type",
            "period",
            "item_title",
            "item_code",
            "category_title",
            "orders_count",
            "quantity_sold",
            "sales_amount",
            "sales_share",
            "avg_unit_price",
        ]
        if details.empty:
            return pd.DataFrame(columns=columns)
        completed = details[_is_completed(details["order_state"])].copy()
        if completed.empty:
            return pd.DataFrame(columns=columns)
        grouped = (
            completed.groupby(["item_title", "item_code", "category_title"], dropna=False)
            .agg(
                orders_count=("order_id", "nunique"),
                quantity_sold=("quantity", "sum"),
                sales_amount=("line_total_amount", "sum"),
            )
            .reset_index()
        )
        total = int(grouped["sales_amount"].sum())
        grouped["period_type"] = period_type
        grouped["period"] = period_label
        grouped["sales_share"] = grouped["sales_amount"].apply(lambda value: _safe_div(value, total))
        grouped["avg_unit_price"] = grouped.apply(lambda row: _safe_div(row["sales_amount"], row["quantity_sold"]), axis=1)
        return grouped[columns]

    def _payment_summary(self, payments: pd.DataFrame, period_type: str, period_label: str) -> pd.DataFrame:
        columns = [
            "period_type",
            "period",
            "payment_method",
            "approved_amount",
            "cancelled_amount",
            "net_payment_amount",
            "payments_count",
            "approved_count",
            "cancelled_count",
            "share",
        ]
        if payments.empty:
            return pd.DataFrame(columns=columns)
        rows = []
        for method, group in payments.groupby("payment_method", dropna=False):
            cancelled = _is_cancelled(group["state"])
            approved_count = int((~cancelled).sum())
            cancelled_count = int(cancelled.sum())
            approved_amount = int(group.loc[~cancelled, "amount"].sum())
            cancelled_amount = int(group.loc[cancelled, "amount"].sum())
            rows.append(
                {
                    "period_type": period_type,
                    "period": period_label,
                    "payment_method": method or "UNDEFINED",
                    "approved_amount": approved_amount,
                    "cancelled_amount": cancelled_amount,
                    "net_payment_amount": approved_amount - cancelled_amount,
                    "payments_count": int(len(group)),
                    "approved_count": approved_count,
                    "cancelled_count": cancelled_count,
                }
            )
        result = pd.DataFrame(rows)
        total = int(result["approved_amount"].sum())
        result["share"] = result["approved_amount"].apply(lambda value: _safe_div(value, total))
        return result[columns]

    def _merchant_timezone(self, session: Session, merchant_id: int) -> ZoneInfo:
        timezone_name = session.scalar(
            text("select timezone from merchants where merchant_id = :merchant_id"),
            {"merchant_id": merchant_id},
        )
        return ZoneInfo(timezone_name or "Asia/Seoul")


COMPLETED_STATES = {"COMPLETED", "DONE", "APPROVED", "PAID"}
CANCELLED_TOKEN = "CANCEL"
TIMESTAMP_COLUMNS = {
    "approved_at",
    "cancelled_at",
    "created_at",
    "updated_at",
    "completed_at",
}


def _is_completed(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.upper().isin(COMPLETED_STATES)


def _is_cancelled(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.upper().str.contains(CANCELLED_TOKEN)


def _safe_div(numerator: float, denominator: float) -> float:
    return float(numerator / denominator) if denominator else 0.0


def _format_timestamp_columns(frame: pd.DataFrame, timezone: ZoneInfo) -> pd.DataFrame:
    if frame.empty:
        return frame
    result = frame.copy()
    for column in TIMESTAMP_COLUMNS.intersection(result.columns):
        values = pd.to_datetime(result[column], errors="coerce", utc=True).dt.tz_convert(timezone)
        result[column] = values.dt.strftime("%Y-%m-%d %H:%M:%S")
        result.loc[values.isna(), column] = ""
    return result
