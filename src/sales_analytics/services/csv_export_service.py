from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from sales_analytics.config import MerchantConfig


@dataclass(frozen=True)
class ExportedFile:
    report_type: str
    path: Path
    checksum: str
    size_bytes: int


class CsvExportService:
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir

    def export(self, merchant: MerchantConfig, business_date: date, frames: dict[str, pd.DataFrame]) -> list[ExportedFile]:
        target_dir = self.output_dir / f"merchant_{merchant.merchant_id}" / str(business_date)
        target_dir.mkdir(parents=True, exist_ok=True)
        merchant_slug = self._slug(merchant.merchant_name)
        exported: list[ExportedFile] = []
        for report_type, frame in frames.items():
            path = target_dir / f"{merchant_slug}_{report_type}_{business_date.isoformat()}.csv"
            frame.to_csv(path, index=False, encoding="utf-8-sig")
            exported.append(
                ExportedFile(
                    report_type=report_type,
                    path=path,
                    checksum=self._sha256(path),
                    size_bytes=path.stat().st_size,
                )
            )
        return exported

    def _slug(self, value: str) -> str:
        slug = re.sub(r"[^A-Za-z0-9가-힣_-]+", "_", value.strip())
        return slug.strip("_") or "merchant"

    def _sha256(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
