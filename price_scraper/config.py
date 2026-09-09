"""Load and validate the YAML scraper configuration.

The config decouples *what* to scrape (URLs + CSS selectors) from the code,
so the same scraper works on any shop by editing ``config.yaml`` only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


class ConfigError(ValueError):
    """Raised when the config file is missing, malformed, or incomplete."""


@dataclass
class RequestSettings:
    user_agent: str = "PortfolioPriceScraper/1.0 (+https://github.com/kwattakoning)"
    timeout: float = 15.0
    delay_seconds: float = 1.0  # politeness pause between page requests
    max_pages: int = 3


@dataclass
class Selectors:
    product: str          # CSS selector for each product container
    name: str             # selector (within a product) for the name
    price: str            # selector for the price text
    availability: str | None = None
    url: str | None = None
    name_attr: str | None = None  # read this attribute instead of text (e.g. "title")
    url_attr: str = "href"


@dataclass
class Pagination:
    next: str | None = None       # selector for the "next page" link
    next_attr: str = "href"


@dataclass
class Config:
    name: str
    start_url: str
    base_url: str
    selectors: Selectors
    pagination: Pagination = field(default_factory=Pagination)
    request: RequestSettings = field(default_factory=RequestSettings)
    currency_symbol: str = ""
    csv_path: str = "prices.csv"


def load_config(path: str | Path) -> Config:
    """Read and validate a YAML config file, returning a :class:`Config`."""
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc
    return _build_config(raw)


def _build_config(raw: dict) -> Config:
    if not isinstance(raw, dict):
        raise ConfigError("Top-level YAML must be a mapping (key: value).")

    site = raw.get("site") or {}
    sel = raw.get("selectors") or {}

    if not site.get("start_url"):
        raise ConfigError("Missing required key: site.start_url")
    for key in ("product", "name", "price"):
        if not sel.get(key):
            raise ConfigError(f"Missing required key: selectors.{key}")

    start_url = site["start_url"]
    selectors = Selectors(
        product=sel["product"],
        name=sel["name"],
        price=sel["price"],
        availability=sel.get("availability"),
        url=sel.get("url"),
        name_attr=sel.get("name_attr"),
        url_attr=sel.get("url_attr", "href"),
    )

    pag = raw.get("pagination") or {}
    pagination = Pagination(next=pag.get("next"), next_attr=pag.get("next_attr", "href"))

    req = raw.get("request") or {}
    request = RequestSettings(
        user_agent=req.get("user_agent", RequestSettings.user_agent),
        timeout=float(req.get("timeout", RequestSettings.timeout)),
        delay_seconds=float(req.get("delay_seconds", RequestSettings.delay_seconds)),
        max_pages=int(req.get("max_pages", RequestSettings.max_pages)),
    )

    out = raw.get("output") or {}
    return Config(
        name=site.get("name", "unnamed site"),
        start_url=start_url,
        base_url=site.get("base_url") or start_url,
        selectors=selectors,
        pagination=pagination,
        request=request,
        currency_symbol=out.get("currency_symbol", ""),
        csv_path=out.get("csv_path", "prices.csv"),
    )
