from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session

from sales_analytics.clients.toss_place_client import TossPlaceClient
from sales_analytics.config import MerchantConfig
from sales_analytics.db.models import RawPosApiResponse
from sales_analytics.services.business_date_service import BusinessDateService


class IngestionService:
    def __init__(self, client: TossPlaceClient, business_dates: BusinessDateService):
        self.client = client
        self.business_dates = business_dates

    def ingest(self, session: Session, merchant: MerchantConfig, business_date: date) -> list[dict[str, Any]]:
        start_at, end_at = self.business_dates.calculate_range(merchant, business_date)
        pages = self.client.fetch_orders(merchant, business_date, start_at, end_at)
        orders: list[dict[str, Any]] = []
        for page in pages:
            session.add(
                RawPosApiResponse(
                    merchant_id=merchant.merchant_id,
                    business_date=business_date,
                    endpoint=page["endpoint"],
                    request_params=page["request_params"],
                    response_body=page["response_body"],
                    http_status=page["http_status"],
                    x_toss_event_id=page.get("x_toss_event_id"),
                )
            )
            orders.extend(page["orders"])
        return orders

    def ingest_range(
        self,
        session: Session,
        merchant: MerchantConfig,
        range_start: date,
        range_end: date,
        start_at: datetime,
        end_at: datetime,
    ) -> dict[date, list[dict[str, Any]]]:
        pages = self.client.fetch_orders(merchant, range_start, start_at, end_at)
        grouped: dict[date, list[dict[str, Any]]] = {}
        for page in pages:
            page_groups: dict[date, list[dict[str, Any]]] = {}
            for order in page["orders"]:
                business_date = self.business_dates.business_date_for_timestamp(
                    merchant,
                    order.get("createdAt") or order.get("created_at") or order.get("openedAt") or start_at,
                )
                if business_date < range_start or business_date > range_end:
                    continue
                grouped.setdefault(business_date, []).append(order)
                page_groups.setdefault(business_date, []).append(order)
            for business_date, orders in page_groups.items():
                session.add(
                    RawPosApiResponse(
                        merchant_id=merchant.merchant_id,
                        business_date=business_date,
                        endpoint=page["endpoint"],
                        request_params=page["request_params"],
                        response_body={**page["response_body"], "orders": orders},
                        http_status=page["http_status"],
                        x_toss_event_id=page.get("x_toss_event_id"),
                    )
                )
        return grouped
