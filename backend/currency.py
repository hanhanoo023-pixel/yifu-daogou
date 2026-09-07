from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml


CurrencyCode = Literal["USD", "CNY"]

CURRENCY_CONFIG_PATH = (
    Path(__file__).resolve().parents[1] / "configs" / "currency.yaml"
)


@lru_cache(maxsize=1)
def load_currency_config() -> dict[str, str | float]:
    config = yaml.safe_load(CURRENCY_CONFIG_PATH.read_text(encoding="utf-8"))
    return {
        "catalog_currency": str(config["catalog_currency"]),
        "cny_per_usd": float(config["cny_per_usd"]),
        "notice": str(config["notice"]),
    }


def to_catalog_price(value: float | None, source_currency: CurrencyCode) -> float | None:
    if value is None:
        return None
    config = load_currency_config()
    if source_currency == config["catalog_currency"]:
        return value
    if source_currency == "CNY" and config["catalog_currency"] == "USD":
        return value / float(config["cny_per_usd"])
    raise ValueError(
        f"Unsupported currency conversion: {source_currency} -> "
        f"{config['catalog_currency']}"
    )


def usd_to_cny(value: float) -> float:
    return value * float(load_currency_config()["cny_per_usd"])
