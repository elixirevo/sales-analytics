from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class Merchant(Base):
    __tablename__ = "merchants"

    merchant_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    merchant_name: Mapped[str] = mapped_column(Text, nullable=False)
    business_number: Mapped[str] = mapped_column(Text, default="")
    timezone: Mapped[str] = mapped_column(Text, default="Asia/Seoul")
    business_open_time: Mapped[str] = mapped_column(Text, default="09:00")
    business_close_time: Mapped[str] = mapped_column(Text, default="22:00")
    drive_folder_id: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class RawPosApiResponse(Base):
    __tablename__ = "raw_pos_api_responses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    merchant_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    business_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    request_params: Mapped[dict] = mapped_column(JSON, nullable=False)
    response_body: Mapped[dict] = mapped_column(JSON, nullable=False)
    http_status: Mapped[int] = mapped_column(Integer, nullable=False)
    x_toss_event_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class Order(Base):
    __tablename__ = "orders"

    order_id: Mapped[str] = mapped_column(Text, primary_key=True)
    merchant_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    business_date: Mapped[datetime.date] = mapped_column(Date, nullable=False, index=True)
    source: Mapped[str | None] = mapped_column(Text)
    order_state: Mapped[str] = mapped_column(Text, nullable=False)
    order_number: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime)
    list_price: Mapped[int] = mapped_column(BigInteger, default=0)
    discount_amount: Mapped[int] = mapped_column(BigInteger, default=0)
    tax_amount: Mapped[int] = mapped_column(BigInteger, default=0)
    supply_amount: Mapped[int] = mapped_column(BigInteger, default=0)
    tax_exempt_amount: Mapped[int] = mapped_column(BigInteger, default=0)
    total_amount: Mapped[int] = mapped_column(BigInteger, default=0)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime)
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class OrderLineItem(Base):
    __tablename__ = "order_line_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    order_id: Mapped[str] = mapped_column(Text, ForeignKey("orders.order_id"), index=True)
    merchant_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    business_date: Mapped[datetime.date] = mapped_column(Date, nullable=False, index=True)
    item_title: Mapped[str] = mapped_column(Text, default="UNKNOWN")
    item_code: Mapped[str | None] = mapped_column(Text)
    category_title: Mapped[str] = mapped_column(Text, default="UNKNOWN")
    dining_option: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[int] = mapped_column(BigInteger, default=0)
    unit_price: Mapped[int] = mapped_column(BigInteger, default=0)
    option_amount: Mapped[int] = mapped_column(BigInteger, default=0)
    line_discount_amount: Mapped[int] = mapped_column(BigInteger, default=0)
    line_total_amount: Mapped[int] = mapped_column(BigInteger, default=0)


class Payment(Base):
    __tablename__ = "payments"

    payment_id: Mapped[str] = mapped_column(Text, primary_key=True)
    order_id: Mapped[str] = mapped_column(Text, ForeignKey("orders.order_id"), index=True)
    merchant_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    business_date: Mapped[datetime.date] = mapped_column(Date, nullable=False, index=True)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(Text, default="UNDEFINED")
    payment_method: Mapped[str] = mapped_column(Text, default="UNDEFINED")
    amount: Mapped[int] = mapped_column(BigInteger, default=0)
    tax_amount: Mapped[int] = mapped_column(BigInteger, default=0)
    supply_amount: Mapped[int] = mapped_column(BigInteger, default=0)
    tax_exempt_amount: Mapped[int] = mapped_column(BigInteger, default=0)
    approved_no: Mapped[str | None] = mapped_column(Text)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime)


class BatchRun(Base):
    __tablename__ = "batch_runs"

    run_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    merchant_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    business_date: Mapped[datetime.date] = mapped_column(Date, nullable=False, index=True)
    status: Mapped[str] = mapped_column(Text, default="RUNNING")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    orders_count: Mapped[int] = mapped_column(Integer, default=0)
    payments_count: Mapped[int] = mapped_column(Integer, default=0)
    csv_files_count: Mapped[int] = mapped_column(Integer, default=0)
    drive_upload_status: Mapped[str] = mapped_column(Text, default="NOT_STARTED")
    error_message: Mapped[str | None] = mapped_column(Text)


class DriveUpload(Base):
    __tablename__ = "drive_uploads"
    __table_args__ = (UniqueConstraint("merchant_id", "business_date", "report_type", "checksum", name="uq_drive_upload_report_checksum"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("batch_runs.run_id"))
    merchant_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    business_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    report_type: Mapped[str] = mapped_column(Text, nullable=False)
    file_name: Mapped[str] = mapped_column(Text, nullable=False)
    drive_file_id: Mapped[str] = mapped_column(Text, nullable=False)
    drive_folder_id: Mapped[str] = mapped_column(Text, nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    checksum: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, default="SUCCESS")
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
