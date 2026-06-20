from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import delete
from sqlalchemy.orm import Session

from sales_analytics.db.models import Order, OrderLineItem, Payment, now_utc


def _pick(data: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return default


def _pick_path(data: dict[str, Any], *path: str, default: Any = None) -> Any:
    current: Any = data
    for key in path:
        if not isinstance(current, dict) or key not in current or current[key] is None:
            return default
        current = current[key]
    return current


def _to_int(value: Any) -> int:
    if value in (None, ""):
        return 0
    return int(value)


def _sum_nested_amount(rows: list[dict[str, Any]], *keys: str) -> int:
    total = 0
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        value = _pick(row, *keys, default=0)
        if isinstance(value, dict):
            value = _pick(value, "value", "amount", "priceValue", default=0)
        if value == 0:
            value = _pick_path(row, "amount", "value", default=0)
        total += _to_int(value)
    return total


def _to_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)


def _is_newer(existing: Order | None, incoming_updated_at: datetime | None) -> bool:
    if existing is None:
        return True
    if incoming_updated_at is None or existing.updated_at is None:
        return True
    return incoming_updated_at > existing.updated_at


class NormalizationService:
    def normalize_orders(
        self,
        session: Session,
        merchant_id: int,
        business_date: date,
        raw_orders: list[dict[str, Any]],
    ) -> tuple[int, int]:
        upserted_orders = 0
        upserted_payments = 0
        for payload in raw_orders:
            order_id = str(_pick(payload, "orderId", "order_id", "id"))
            if not order_id or order_id == "None":
                continue
            amounts = _pick(payload, "amounts", "amount", "chargePrice", default={}) or {}
            updated_at = _to_dt(_pick(payload, "updatedAt", "updated_at"))
            existing = session.get(Order, order_id)
            if not _is_newer(existing, updated_at):
                continue

            order = existing or Order(order_id=order_id)
            order.merchant_id = merchant_id
            order.business_date = business_date
            order.source = _pick(payload, "source", "channel")
            order.order_state = str(_pick(payload, "orderState", "order_state", "state", default="UNKNOWN"))
            order.order_number = _pick(payload, "orderNumber", "order_number")
            order.created_at = _to_dt(_pick(payload, "createdAt", "created_at"))
            order.completed_at = _to_dt(_pick(payload, "completedAt", "completed_at"))
            order.cancelled_at = _to_dt(_pick(payload, "cancelledAt", "cancelled_at"))
            order.list_price = _to_int(_pick(amounts, "listPrice", "list_price", default=_pick(payload, "listPrice")))
            order.discount_amount = _to_int(
                _pick(amounts, "discountAmount", "discount_amount", default=_pick(payload, "discountAmount"))
            )
            order.tax_amount = _to_int(_pick(amounts, "taxAmount", "tax_amount", default=_pick(payload, "taxAmount")))
            order.supply_amount = _to_int(
                _pick(amounts, "supplyAmount", "supply_amount", default=_pick(payload, "supplyAmount"))
            )
            order.tax_exempt_amount = _to_int(
                _pick(amounts, "taxExemptAmount", "tax_exempt_amount", default=_pick(payload, "taxExemptAmount"))
            )
            order.total_amount = _to_int(
                _pick(amounts, "totalAmount", "total_amount", default=_pick(payload, "totalAmount"))
            )
            order.updated_at = updated_at
            order.ingested_at = now_utc()
            session.merge(order)
            session.flush()

            session.execute(delete(OrderLineItem).where(OrderLineItem.order_id == order_id))
            for line in _pick(payload, "lineItems", "line_items", "items", default=[]) or []:
                unit_price = _to_int(
                    _pick(
                        line,
                        "unitPrice",
                        "unit_price",
                        default=_pick_path(line, "itemPrice", "priceValue", default=0),
                    )
                )
                option_amount = _to_int(_pick(line, "optionAmount", "option_amount", default=0))
                if not option_amount:
                    option_amount = _sum_nested_amount(_pick(line, "optionChoices", "option_choices", default=[]), "priceValue", "amount")
                line_discount_amount = _to_int(_pick(line, "lineDiscountAmount", "line_discount_amount", default=0))
                if not line_discount_amount:
                    line_discount_amount = _sum_nested_amount(
                        _pick(line, "appliedDiscounts", "applied_discounts", default=[]),
                        "discountAmount",
                        "amount",
                    )
                quantity = _to_int(_pick(line, "quantity", "qty", default=0))
                line_total_amount = _to_int(_pick(line, "lineTotalAmount", "line_total_amount", default=0))
                if not line_total_amount:
                    line_total_amount = max(0, (unit_price + option_amount) * quantity - line_discount_amount)
                session.add(
                    OrderLineItem(
                        order_id=order_id,
                        merchant_id=merchant_id,
                        business_date=business_date,
                        item_title=str(
                            _pick(
                                line,
                                "itemTitle",
                                "item_title",
                                "title",
                                "name",
                                default=_pick_path(line, "item", "title", default="UNKNOWN"),
                            )
                            or "UNKNOWN"
                        ),
                        item_code=_pick(line, "itemCode", "item_code", "code", default=_pick_path(line, "item", "code")),
                        category_title=str(
                            _pick(
                                line,
                                "categoryTitle",
                                "category_title",
                                "category",
                                default=_pick_path(line, "item", "category", "title", default="UNKNOWN"),
                            )
                            or "UNKNOWN"
                        ),
                        dining_option=_pick(line, "diningOption", "dining_option"),
                        quantity=quantity,
                        unit_price=unit_price,
                        option_amount=option_amount,
                        line_discount_amount=line_discount_amount,
                        line_total_amount=line_total_amount,
                    )
                )

            for payment in _pick(payload, "payments", "paymentList", default=[]) or []:
                payment_id = str(_pick(payment, "paymentId", "payment_id", "id", default=f"{order_id}-payment"))
                row = session.get(Payment, payment_id) or Payment(payment_id=payment_id)
                row.order_id = order_id
                row.merchant_id = merchant_id
                row.business_date = business_date
                row.state = str(_pick(payment, "state", "paymentState", default="UNKNOWN"))
                row.source_type = str(_pick(payment, "sourceType", "source_type", default="UNDEFINED") or "UNDEFINED")
                row.payment_method = str(
                    _pick(payment, "paymentMethod", "payment_method", "method", default="UNDEFINED") or "UNDEFINED"
                )
                row.amount = _to_int(_pick(payment, "amount", "paymentAmount", default=0))
                row.tax_amount = _to_int(_pick(payment, "taxAmount", "tax_amount", default=0))
                row.supply_amount = _to_int(_pick(payment, "supplyAmount", "supply_amount", default=0))
                row.tax_exempt_amount = _to_int(_pick(payment, "taxExemptAmount", "tax_exempt_amount", default=0))
                row.approved_no = _pick(payment, "approvedNo", "approved_no")
                row.approved_at = _to_dt(_pick(payment, "approvedAt", "approved_at"))
                row.cancelled_at = _to_dt(_pick(payment, "cancelledAt", "cancelled_at"))
                row.created_at = _to_dt(_pick(payment, "createdAt", "created_at"))
                row.updated_at = _to_dt(_pick(payment, "updatedAt", "updated_at"))
                session.merge(row)
                upserted_payments += 1
            upserted_orders += 1
        return upserted_orders, upserted_payments
