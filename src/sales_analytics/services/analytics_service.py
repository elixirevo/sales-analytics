from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session


COMPLETED_STATES = {"COMPLETED", "DONE", "APPROVED", "PAID"}
CANCELLED_TOKEN = "CANCEL"


@dataclass(frozen=True)
class AnalyticsResult:
    frames: dict[str, pd.DataFrame]
    quality_warnings: pd.DataFrame


def _is_completed(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.upper().isin(COMPLETED_STATES)


def _is_cancelled(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.upper().str.contains(CANCELLED_TOKEN)


def _safe_div(numerator: float, denominator: float) -> float:
    return float(numerator / denominator) if denominator else 0.0


class AnalyticsService:
    def build_reports(self, session: Session, merchant_id: int, business_date: date) -> AnalyticsResult:
        orders = self._read_sql(session, "orders", merchant_id, business_date)
        items = self._read_items(session, merchant_id, business_date)
        payments = self._read_sql(session, "payments", merchant_id, business_date)
        prior_items = self._read_items(session, merchant_id, business_date - timedelta(days=1))
        week_items = self._read_items(session, merchant_id, business_date - timedelta(days=7))
        week_orders = self._read_sql(session, "orders", merchant_id, business_date - timedelta(days=7))
        recent_items = self._read_items_range(session, merchant_id, business_date - timedelta(days=7), business_date)

        frames = {
            "raw_orders": orders,
            "raw_payments": payments,
            "daily_sales_summary": self._daily_summary(merchant_id, business_date, orders),
            "hourly_sales": self._hourly_sales(business_date, orders),
            "item_sales": self._item_sales(items, prior_items, week_items),
            "category_sales": self._category_sales(items),
            "payment_method_sales": self._payment_method_sales(payments),
        }
        frames["management_alerts"] = self._management_alerts(
            frames["daily_sales_summary"], orders, items, week_orders, week_items, recent_items
        )
        warnings = self._quality_warnings(orders, payments)
        if not warnings.empty:
            frames["data_quality_warnings"] = warnings
        return AnalyticsResult(frames=frames, quality_warnings=warnings)

    def _read_sql(self, session: Session, table: str, merchant_id: int, business_date: date) -> pd.DataFrame:
        return pd.read_sql_query(
            text(f"select * from {table} where merchant_id = :merchant_id and business_date = :business_date"),
            session.connection(),
            params={"merchant_id": merchant_id, "business_date": business_date},
        )

    def _read_items(self, session: Session, merchant_id: int, business_date: date) -> pd.DataFrame:
        query = text(
            """
            select li.*, o.order_state
            from order_line_items li
            join orders o on o.order_id = li.order_id
            where li.merchant_id = :merchant_id and li.business_date = :business_date
            """
        )
        return pd.read_sql_query(query, session.connection(), params={"merchant_id": merchant_id, "business_date": business_date})

    def _read_items_range(self, session: Session, merchant_id: int, start_date: date, end_date: date) -> pd.DataFrame:
        query = text(
            """
            select li.*, o.order_state
            from order_line_items li
            join orders o on o.order_id = li.order_id
            where li.merchant_id = :merchant_id and li.business_date >= :start_date and li.business_date <= :end_date
            """
        )
        return pd.read_sql_query(
            query,
            session.connection(),
            params={"merchant_id": merchant_id, "start_date": start_date, "end_date": end_date},
        )

    def _daily_summary(self, merchant_id: int, business_date: date, orders: pd.DataFrame) -> pd.DataFrame:
        if orders.empty:
            return pd.DataFrame([self._empty_summary(merchant_id, business_date)])
        completed = orders[_is_completed(orders["order_state"])]
        cancelled = orders[_is_cancelled(orders["order_state"])]
        gross = int(completed["total_amount"].sum())
        cancelled_sales = int(cancelled["total_amount"].sum())
        order_count = int(len(completed))
        cancelled_count = int(len(cancelled))
        list_price = int(completed["list_price"].sum())
        discount = int(completed["discount_amount"].sum())
        return pd.DataFrame(
            [
                {
                    "merchant_id": merchant_id,
                    "business_date": business_date.isoformat(),
                    "gross_sales": gross,
                    "net_sales": gross - cancelled_sales,
                    "orders_count": order_count,
                    "cancelled_orders_count": cancelled_count,
                    "cancel_rate": _safe_div(cancelled_count, len(orders)),
                    "avg_order_value": _safe_div(gross, order_count),
                    "discount_amount": discount,
                    "discount_rate": _safe_div(discount, list_price),
                    "tax_amount": int(completed["tax_amount"].sum()),
                    "supply_amount": int(completed["supply_amount"].sum()),
                }
            ]
        )

    def _hourly_sales(self, business_date: date, orders: pd.DataFrame) -> pd.DataFrame:
        base = pd.DataFrame({"hour": list(range(24))})
        if orders.empty:
            base["business_date"] = business_date.isoformat()
            base["sales_amount"] = 0
            base["orders_count"] = 0
            base["avg_order_value"] = 0.0
            base["cancelled_orders_count"] = 0
            base["sales_share"] = 0.0
            return base[["business_date", "hour", "sales_amount", "orders_count", "avg_order_value", "cancelled_orders_count", "sales_share"]]

        orders = orders.copy()
        orders["hour"] = pd.to_datetime(orders["created_at"], errors="coerce").dt.hour.fillna(0).astype(int)
        completed = orders[_is_completed(orders["order_state"])]
        cancelled = orders[_is_cancelled(orders["order_state"])]
        sales = completed.groupby("hour").agg(sales_amount=("total_amount", "sum"), orders_count=("order_id", "count")).reset_index()
        cancels = cancelled.groupby("hour").agg(cancelled_orders_count=("order_id", "count")).reset_index()
        result = base.merge(sales, on="hour", how="left").merge(cancels, on="hour", how="left").fillna(0)
        result["business_date"] = business_date.isoformat()
        result["sales_amount"] = result["sales_amount"].astype(int)
        result["orders_count"] = result["orders_count"].astype(int)
        result["cancelled_orders_count"] = result["cancelled_orders_count"].astype(int)
        result["avg_order_value"] = result.apply(lambda row: _safe_div(row["sales_amount"], row["orders_count"]), axis=1)
        total = int(result["sales_amount"].sum())
        result["sales_share"] = result["sales_amount"].apply(lambda value: _safe_div(value, total))
        return result[["business_date", "hour", "sales_amount", "orders_count", "avg_order_value", "cancelled_orders_count", "sales_share"]]

    def _item_sales(self, items: pd.DataFrame, prior_items: pd.DataFrame, week_items: pd.DataFrame) -> pd.DataFrame:
        current = self._completed_items(items)
        if current.empty:
            return pd.DataFrame(
                columns=[
                    "item_title",
                    "category_title",
                    "quantity_sold",
                    "sales_amount",
                    "sales_share",
                    "avg_unit_price",
                    "dod_quantity_change",
                    "wow_sales_growth_rate",
                ]
            )
        grouped = (
            current.groupby(["item_title", "category_title"], dropna=False)
            .agg(quantity_sold=("quantity", "sum"), sales_amount=("line_total_amount", "sum"))
            .reset_index()
        )
        prior = self._completed_items(prior_items)
        week = self._completed_items(week_items)
        prior_grouped = (
            prior.groupby("item_title").agg(prior_quantity=("quantity", "sum")).reset_index()
            if not prior.empty
            else pd.DataFrame(columns=["item_title", "prior_quantity"])
        )
        week_grouped = (
            week.groupby("item_title").agg(week_sales=("line_total_amount", "sum")).reset_index()
            if not week.empty
            else pd.DataFrame(columns=["item_title", "week_sales"])
        )
        result = grouped.merge(prior_grouped, on="item_title", how="left").merge(week_grouped, on="item_title", how="left").fillna(0)
        total = int(result["sales_amount"].sum())
        result["sales_share"] = result["sales_amount"].apply(lambda value: _safe_div(value, total))
        result["avg_unit_price"] = result.apply(lambda row: _safe_div(row["sales_amount"], row["quantity_sold"]), axis=1)
        result["dod_quantity_change"] = result["quantity_sold"] - result["prior_quantity"]
        result["wow_sales_growth_rate"] = result.apply(
            lambda row: _safe_div(row["sales_amount"], row["week_sales"]) - 1 if row["week_sales"] else 0.0,
            axis=1,
        )
        return result[
            [
                "item_title",
                "category_title",
                "quantity_sold",
                "sales_amount",
                "sales_share",
                "avg_unit_price",
                "dod_quantity_change",
                "wow_sales_growth_rate",
            ]
        ]

    def _category_sales(self, items: pd.DataFrame) -> pd.DataFrame:
        current = self._completed_items(items)
        if current.empty:
            return pd.DataFrame(columns=["category_title", "sales_amount", "orders_count", "quantity_sold", "sales_share"])
        result = (
            current.groupby("category_title", dropna=False)
            .agg(sales_amount=("line_total_amount", "sum"), orders_count=("order_id", "nunique"), quantity_sold=("quantity", "sum"))
            .reset_index()
        )
        total = int(result["sales_amount"].sum())
        result["sales_share"] = result["sales_amount"].apply(lambda value: _safe_div(value, total))
        return result

    def _payment_method_sales(self, payments: pd.DataFrame) -> pd.DataFrame:
        if payments.empty:
            return pd.DataFrame(columns=["payment_method", "approved_amount", "cancelled_amount", "net_payment_amount", "payments_count", "share"])
        rows = []
        for method, group in payments.groupby("payment_method", dropna=False):
            cancelled = _is_cancelled(group["state"])
            approved_amount = int(group.loc[~cancelled, "amount"].sum())
            cancelled_amount = int(group.loc[cancelled, "amount"].sum())
            rows.append(
                {
                    "payment_method": method or "UNDEFINED",
                    "approved_amount": approved_amount,
                    "cancelled_amount": cancelled_amount,
                    "net_payment_amount": approved_amount - cancelled_amount,
                    "payments_count": int(len(group)),
                }
            )
        result = pd.DataFrame(rows)
        total = int(result["approved_amount"].sum())
        result["share"] = result["approved_amount"].apply(lambda value: _safe_div(value, total))
        return result[["payment_method", "approved_amount", "cancelled_amount", "net_payment_amount", "payments_count", "share"]]

    def _management_alerts(
        self,
        summary: pd.DataFrame,
        orders: pd.DataFrame,
        items: pd.DataFrame,
        week_orders: pd.DataFrame,
        week_items: pd.DataFrame,
        recent_items: pd.DataFrame,
    ) -> pd.DataFrame:
        alerts: list[dict] = []
        row = summary.iloc[0]
        week_summary = self._daily_summary(int(row["merchant_id"]), date.fromisoformat(str(row["business_date"])), week_orders)
        week_sales = float(week_summary.iloc[0]["gross_sales"]) if not week_summary.empty else 0.0
        if week_sales and _safe_div(float(row["gross_sales"]), week_sales) - 1 <= -0.2:
            alerts.append(self._alert("SALES_DROP", "HIGH", "gross_sales", row["gross_sales"], week_sales, "전주 동일 요일 대비 매출이 20% 이상 감소했습니다."))
        if float(row["cancel_rate"]) > 0.05:
            alerts.append(self._alert("HIGH_CANCEL_RATE", "MEDIUM", "cancel_rate", row["cancel_rate"], 0.05, "취소율이 5%를 초과했습니다."))
        if float(row["discount_rate"]) > 0.15:
            alerts.append(self._alert("HIGH_DISCOUNT_RATE", "MEDIUM", "discount_rate", row["discount_rate"], 0.15, "할인율이 15%를 초과했습니다."))
        hourly = self._hourly_sales(date.fromisoformat(str(row["business_date"])), orders)
        gross = float(row["gross_sales"])
        top_two_share = _safe_div(float(hourly.nlargest(2, "sales_amount")["sales_amount"].sum()), gross)
        if gross and top_two_share > 0.5:
            alerts.append(self._alert("PEAK_CONCENTRATION", "LOW", "top_two_hour_sales_share", top_two_share, 0.5, "상위 2개 시간대 매출 비중이 50%를 초과했습니다."))

        current_items = self._item_sales(items, pd.DataFrame(), week_items)
        if not current_items.empty:
            for item in current_items[current_items["wow_sales_growth_rate"] <= -0.3].to_dict("records"):
                alerts.append(
                    self._alert(
                        "ITEM_DROP",
                        "MEDIUM",
                        f"wow_sales_growth_rate:{item['item_title']}",
                        item["wow_sales_growth_rate"],
                        -0.3,
                        f"{item['item_title']} 매출이 전주 동일 요일 대비 30% 이상 감소했습니다.",
                    )
                )
        recent = self._completed_items(recent_items)
        if not recent.empty:
            low = recent.groupby("item_title").agg(quantity=("quantity", "sum")).reset_index()
            for item in low[low["quantity"] <= 1].to_dict("records"):
                alerts.append(
                    self._alert(
                        "LOW_PERFORMING_ITEM",
                        "LOW",
                        f"seven_day_quantity:{item['item_title']}",
                        item["quantity"],
                        1,
                        f"{item['item_title']} 최근 7일 판매량이 낮습니다.",
                    )
                )
        return pd.DataFrame(alerts, columns=["alert_type", "severity", "metric_name", "metric_value", "baseline_value", "message"])

    def _quality_warnings(self, orders: pd.DataFrame, payments: pd.DataFrame) -> pd.DataFrame:
        warnings: list[dict] = []
        if orders.empty:
            warnings.append(self._warning("NO_ORDERS", "HIGH", None, "orders_count", 0, None, "대상 영업일 주문 데이터가 없습니다."))
            return pd.DataFrame(warnings)
        for _, order in orders[_is_completed(orders["order_state"])].iterrows():
            matching = payments[payments["order_id"] == order["order_id"]] if not payments.empty else pd.DataFrame()
            approved = matching[~_is_cancelled(matching["state"])] if not matching.empty else matching
            payment_total = int(approved["amount"].sum()) if not approved.empty else 0
            if int(order["total_amount"]) != payment_total:
                warnings.append(
                    self._warning(
                        "ORDER_PAYMENT_MISMATCH",
                        "HIGH",
                        order["order_id"],
                        "payment_amount",
                        payment_total,
                        int(order["total_amount"]),
                        "완료 주문 총액과 승인 결제 합계가 일치하지 않습니다.",
                    )
                )
        return pd.DataFrame(warnings)

    def _completed_items(self, items: pd.DataFrame) -> pd.DataFrame:
        if items.empty:
            return items
        return items[_is_completed(items["order_state"])].copy()

    def _empty_summary(self, merchant_id: int, business_date: date) -> dict:
        return {
            "merchant_id": merchant_id,
            "business_date": business_date.isoformat(),
            "gross_sales": 0,
            "net_sales": 0,
            "orders_count": 0,
            "cancelled_orders_count": 0,
            "cancel_rate": 0.0,
            "avg_order_value": 0.0,
            "discount_amount": 0,
            "discount_rate": 0.0,
            "tax_amount": 0,
            "supply_amount": 0,
        }

    def _alert(self, alert_type: str, severity: str, metric_name: str, metric_value: float, baseline_value: float, message: str) -> dict:
        return {
            "alert_type": alert_type,
            "severity": severity,
            "metric_name": metric_name,
            "metric_value": metric_value,
            "baseline_value": baseline_value,
            "message": message,
        }

    def _warning(
        self,
        warning_type: str,
        severity: str,
        entity_id: str | None,
        metric_name: str,
        metric_value: float,
        baseline_value: float | None,
        message: str,
    ) -> dict:
        return {
            "warning_type": warning_type,
            "severity": severity,
            "entity_id": entity_id,
            "metric_name": metric_name,
            "metric_value": metric_value,
            "baseline_value": baseline_value,
            "message": message,
        }
