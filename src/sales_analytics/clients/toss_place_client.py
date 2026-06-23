from __future__ import annotations

import hashlib
import random
import time
from datetime import date, datetime, timedelta
from typing import Any

import httpx

from sales_analytics.config import MerchantConfig, Settings


class TossPlaceApiError(RuntimeError):
    pass


class TossPlaceClient:
    def fetch_merchant(self, merchant: MerchantConfig) -> dict[str, Any]:
        raise NotImplementedError

    def fetch_orders(
        self,
        merchant: MerchantConfig,
        business_date: date,
        start_at: datetime,
        end_at: datetime,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError


class HttpTossPlaceClient(TossPlaceClient):
    def __init__(self, settings: Settings):
        if not settings.toss_base_url:
            raise ValueError("TOSS_BASE_URL is required when TOSS_CLIENT_MODE=http")
        self.settings = settings

    def fetch_merchant(self, merchant: MerchantConfig) -> dict[str, Any]:
        body, _, _ = self._get(f"/merchants/{merchant.merchant_id}", {})
        success = body.get("success")
        return success if isinstance(success, dict) else body

    def fetch_orders(
        self,
        merchant: MerchantConfig,
        business_date: date,
        start_at: datetime,
        end_at: datetime,
    ) -> list[dict[str, Any]]:
        pages: list[dict[str, Any]] = []
        page = 1
        while True:
            params = {
                "from": start_at.isoformat(),
                "to": end_at.isoformat(),
                "orderStates": "COMPLETED,CANCELLED",
                "page": page,
                "size": self.settings.toss_page_size,
                "sortOrder": "ASC",
            }
            path = f"/merchants/{merchant.merchant_id}/order/orders"
            body, status, event_id = self._get(path, params)
            items = _extract_orders(body)
            pages.append(
                {
                    "endpoint": path,
                    "request_params": params,
                    "response_body": body,
                    "http_status": status,
                    "x_toss_event_id": event_id,
                    "orders": items,
                }
            )
            pager = _extract_pager(body)
            has_next = pager.get("hasNext")
            total_pages = pager.get("totalPages")
            if has_next is False or (total_pages and page >= int(total_pages)) or not items:
                break
            page += 1
        return pages

    def _get(self, path: str, params: dict[str, Any]) -> tuple[dict[str, Any], int, str | None]:
        headers = {
            "x-access-key": self.settings.toss_api_key,
            "x-secret-key": self.settings.toss_api_secret,
            "Content-Type": "application/json",
        }
        last_error: Exception | None = None
        for attempt in range(1, self.settings.retry_max_attempts + 1):
            try:
                with httpx.Client(timeout=30) as client:
                    response = client.get(f"{self.settings.toss_base_url}{path}", params=params, headers=headers)
            except httpx.HTTPError as exc:
                last_error = TossPlaceApiError(f"Toss API request error: {exc}")
                time.sleep(min(60, (2**attempt) + random.random()))
                continue
            if response.status_code == 401:
                raise TossPlaceApiError("Toss API authentication failed with HTTP 401")
            if response.status_code == 429 or 500 <= response.status_code < 600:
                last_error = TossPlaceApiError(f"Toss API retryable status {response.status_code}")
                time.sleep(min(60, (2**attempt) + random.random()))
                continue
            if 400 <= response.status_code < 500:
                raise TossPlaceApiError(
                    f"Toss API client error {response.status_code}: {response.text[:500]}"
                )
            response.raise_for_status()
            return response.json(), response.status_code, response.headers.get("x-toss-event-id")
        raise TossPlaceApiError(str(last_error) if last_error else "Toss API request failed")


class MockTossPlaceClient(TossPlaceClient):
    def fetch_merchant(self, merchant: MerchantConfig) -> dict[str, Any]:
        return {
            "id": merchant.merchant_id,
            "name": merchant.merchant_name,
            "businessNumber": merchant.business_number,
            "operatingHours": [],
        }

    def fetch_orders(
        self,
        merchant: MerchantConfig,
        business_date: date,
        start_at: datetime,
        end_at: datetime,
    ) -> list[dict[str, Any]]:
        orders = self._build_orders(merchant, business_date, start_at)
        body = {"orders": orders, "page": 1, "size": len(orders), "hasNext": False}
        return [
            {
                "endpoint": "/mock/orders",
                "request_params": {
                    "merchantId": merchant.merchant_id,
                    "from": start_at.isoformat(),
                    "to": end_at.isoformat(),
                    "orderStates": "COMPLETED,CANCELLED",
                    "page": 1,
                    "size": len(orders),
                    "sortOrder": "ASC",
                },
                "response_body": body,
                "http_status": 200,
                "x_toss_event_id": f"mock-{merchant.merchant_id}-{business_date}",
                "orders": orders,
            }
        ]

    def _build_orders(self, merchant: MerchantConfig, business_date: date, start_at: datetime) -> list[dict[str, Any]]:
        seed = int(hashlib.sha256(f"{merchant.merchant_id}:{business_date}".encode()).hexdigest()[:8], 16)
        rng = random.Random(seed)
        catalog = [
            ("AMERICANO", "Americano", "Coffee", 4500),
            ("LATTE", "Cafe Latte", "Coffee", 5200),
            ("VANILLA", "Vanilla Latte", "Coffee", 5800),
            ("BAGEL", "Bagel", "Bakery", 4200),
            ("CAKE", "Cake Slice", "Dessert", 6800),
            ("ADE", "Fruit Ade", "Beverage", 6200),
        ]
        count = 36 + rng.randint(0, 24)
        orders: list[dict[str, Any]] = []
        for index in range(count):
            created_at = start_at + timedelta(minutes=15 * index + rng.randint(0, 12))
            state = "CANCELLED" if rng.random() < 0.07 else "COMPLETED"
            line_items = []
            list_price = 0
            for _ in range(1 + rng.randint(0, 2)):
                code, title, category, unit_price = rng.choice(catalog)
                quantity = 1 + rng.randint(0, 2)
                option_amount = rng.choice([0, 0, 500])
                line_total = (unit_price + option_amount) * quantity
                list_price += line_total
                line_items.append(
                    {
                        "itemTitle": title,
                        "itemCode": code,
                        "categoryTitle": category,
                        "diningOption": rng.choice(["DINE_IN", "TAKE_OUT", "DELIVERY"]),
                        "quantity": quantity,
                        "unitPrice": unit_price,
                        "optionAmount": option_amount,
                        "lineDiscountAmount": 0,
                        "lineTotalAmount": line_total,
                    }
                )
            discount_amount = 1000 if rng.random() < 0.12 else 0
            total_amount = max(0, list_price - discount_amount)
            supply_amount = round(total_amount / 1.1)
            tax_amount = total_amount - supply_amount
            order_id = f"mock-{merchant.merchant_id}-{business_date}-{index + 1:04d}"
            payment = {
                "paymentId": f"pay-{order_id}",
                "state": "CANCELLED" if state == "CANCELLED" else "APPROVED",
                "sourceType": "POS",
                "paymentMethod": rng.choice(["CARD", "CASH", "EASY_PAY", "TRANSFER"]),
                "amount": total_amount,
                "taxAmount": tax_amount,
                "supplyAmount": supply_amount,
                "taxExemptAmount": 0,
                "approvedNo": f"{rng.randint(100000, 999999)}",
                "approvedAt": created_at.isoformat(),
                "cancelledAt": (created_at + timedelta(minutes=5)).isoformat() if state == "CANCELLED" else None,
                "createdAt": created_at.isoformat(),
                "updatedAt": created_at.isoformat(),
            }
            orders.append(
                {
                    "orderId": order_id,
                    "merchantId": merchant.merchant_id,
                    "source": rng.choice(["POS", "KIOSK"]),
                    "orderState": state,
                    "orderNumber": f"{index + 1:03d}",
                    "createdAt": created_at.isoformat(),
                    "completedAt": created_at.isoformat() if state == "COMPLETED" else None,
                    "cancelledAt": (created_at + timedelta(minutes=5)).isoformat() if state == "CANCELLED" else None,
                    "amounts": {
                        "listPrice": list_price,
                        "discountAmount": discount_amount,
                        "taxAmount": tax_amount,
                        "supplyAmount": supply_amount,
                        "taxExemptAmount": 0,
                        "totalAmount": total_amount,
                    },
                    "lineItems": line_items,
                    "payments": [payment],
                    "updatedAt": created_at.isoformat(),
                }
            )
        return orders


def build_toss_client(settings: Settings) -> TossPlaceClient:
    if settings.toss_client_mode == "http":
        return HttpTossPlaceClient(settings)
    return MockTossPlaceClient()


def _extract_orders(body: dict[str, Any]) -> list[dict[str, Any]]:
    success = body.get("success")
    if isinstance(success, list):
        return success
    if isinstance(success, dict):
        for key in ("orders", "content", "items", "data"):
            value = success.get(key)
            if isinstance(value, list):
                return value
    for key in ("orders", "content", "items", "data"):
        value = body.get(key)
        if isinstance(value, list):
            return value
    return []


def _extract_pager(body: dict[str, Any]) -> dict[str, Any]:
    success = body.get("success")
    if isinstance(success, dict):
        return success
    return body
