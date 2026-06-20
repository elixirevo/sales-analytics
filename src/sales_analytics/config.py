from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


@dataclass(frozen=True)
class MerchantConfig:
    merchant_id: int
    merchant_name: str
    business_number: str = ""
    timezone: str = "Asia/Seoul"
    business_open_time: str = "09:00"
    business_close_time: str = "22:00"
    drive_folder_id: str = "local-root"
    is_active: bool = True


@dataclass(frozen=True)
class Settings:
    database_url: str
    output_dir: Path
    local_drive_dir: Path
    upload_mode: str
    toss_client_mode: str
    toss_base_url: str
    toss_api_key: str
    toss_api_secret: str
    toss_page_size: int
    retry_max_attempts: int
    google_credentials_file: str
    google_oauth_client_secrets_file: str
    google_oauth_token_file: Path
    google_oauth_auto_auth: bool
    scheduler_close_delay_minutes: int
    scheduler_lookback_days: int
    bootstrap_backfill_on_start: bool
    bootstrap_discovery_enabled: bool
    bootstrap_discovery_lookback_years: int
    bootstrap_backfill_end_offset_days: int
    aggregate_reports_enabled: bool
    merchants: tuple[MerchantConfig, ...]


def _load_merchants(value: str | None) -> tuple[MerchantConfig, ...]:
    if not value:
        return (
            MerchantConfig(
                merchant_id=1,
                merchant_name="Demo Store",
                business_number="000-00-00000",
                drive_folder_id="local-root",
            ),
        )
    raw: Any = json.loads(value)
    if not isinstance(raw, list):
        raise ValueError("MERCHANTS_JSON must be a JSON list")
    return tuple(MerchantConfig(**merchant) for merchant in raw)


def load_settings() -> Settings:
    load_dotenv()
    return Settings(
        database_url=os.getenv("DATABASE_URL", "sqlite:///data/sales_analytics.db"),
        output_dir=Path(os.getenv("OUTPUT_DIR", "reports")),
        local_drive_dir=Path(os.getenv("LOCAL_DRIVE_DIR", "uploads")),
        upload_mode=os.getenv("UPLOAD_MODE", "local").lower(),
        toss_client_mode=os.getenv("TOSS_CLIENT_MODE", "mock").lower(),
        toss_base_url=os.getenv("TOSS_BASE_URL", "").rstrip("/"),
        toss_api_key=os.getenv("TOSS_API_KEY", ""),
        toss_api_secret=os.getenv("TOSS_API_SECRET", ""),
        toss_page_size=int(os.getenv("TOSS_PAGE_SIZE", "100")),
        retry_max_attempts=int(os.getenv("RETRY_MAX_ATTEMPTS", "4")),
        google_credentials_file=os.getenv("GOOGLE_APPLICATION_CREDENTIALS", ""),
        google_oauth_client_secrets_file=os.getenv("GOOGLE_OAUTH_CLIENT_SECRETS_FILE", ""),
        google_oauth_token_file=Path(os.getenv("GOOGLE_OAUTH_TOKEN_FILE", "data/google_oauth_token.json")),
        google_oauth_auto_auth=os.getenv("GOOGLE_OAUTH_AUTO_AUTH", "true").lower() in {"1", "true", "yes", "on"},
        scheduler_close_delay_minutes=int(os.getenv("SCHEDULER_CLOSE_DELAY_MINUTES", "60")),
        scheduler_lookback_days=int(os.getenv("SCHEDULER_LOOKBACK_DAYS", "2")),
        bootstrap_backfill_on_start=os.getenv("BOOTSTRAP_BACKFILL_ON_START", "true").lower() in {"1", "true", "yes", "on"},
        bootstrap_discovery_enabled=os.getenv("BOOTSTRAP_DISCOVERY_ENABLED", "true").lower() in {"1", "true", "yes", "on"},
        bootstrap_discovery_lookback_years=int(os.getenv("BOOTSTRAP_DISCOVERY_LOOKBACK_YEARS", "5")),
        bootstrap_backfill_end_offset_days=int(os.getenv("BOOTSTRAP_BACKFILL_END_OFFSET_DAYS", "1")),
        aggregate_reports_enabled=os.getenv("AGGREGATE_REPORTS_ENABLED", "true").lower() in {"1", "true", "yes", "on"},
        merchants=_load_merchants(os.getenv("MERCHANTS_JSON")),
    )
