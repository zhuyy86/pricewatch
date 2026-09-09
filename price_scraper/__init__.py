"""Configurable daily competitor price scraper.

A small, dependency-light tool that scrapes product names and prices from a
listing page (configured via YAML + CSS selectors) and appends a dated
snapshot to a CSV, building a price history over time.
"""
from .config import Config, ConfigError, load_config
from .scraper import Product, extract_products, find_next_url, parse_price, scrape
from .storage import write_rows

__version__ = "1.0.0"
__all__ = [
    "Config",
    "ConfigError",
    "load_config",
    "Product",
    "extract_products",
    "find_next_url",
    "parse_price",
    "scrape",
    "write_rows",
]
