import textwrap

import pytest

from price_scraper.config import ConfigError, load_config

VALID = """
    site:
      name: Demo
      start_url: https://books.toscrape.com/catalogue/page-1.html
      base_url: https://books.toscrape.com/catalogue/
    selectors:
      product: article.product_pod
      name: h3 a
      name_attr: title
      price: p.price_color
      availability: p.instock.availability
    pagination:
      next: li.next a
    request:
      max_pages: 5
      delay_seconds: 0.5
    output:
      csv_path: out.csv
      currency_symbol: "GBP"
"""


def _write(tmp_path, text):
    path = tmp_path / "config.yaml"
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


def test_load_valid(tmp_path):
    cfg = load_config(_write(tmp_path, VALID))
    assert cfg.name == "Demo"
    assert cfg.selectors.product == "article.product_pod"
    assert cfg.selectors.name_attr == "title"
    assert cfg.pagination.next == "li.next a"
    assert cfg.request.max_pages == 5
    assert cfg.request.delay_seconds == 0.5
    assert cfg.csv_path == "out.csv"
    assert cfg.currency_symbol == "GBP"


def test_defaults_applied(tmp_path):
    minimal = """
        site:
          start_url: https://example.com/
        selectors:
          product: .p
          name: .n
          price: .price
    """
    cfg = load_config(_write(tmp_path, minimal))
    assert cfg.base_url == "https://example.com/"   # falls back to start_url
    assert cfg.request.max_pages == 3               # default
    assert cfg.csv_path == "prices.csv"             # default


def test_missing_required_selector(tmp_path):
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, """
            site:
              start_url: https://example.com/
            selectors:
              product: .p
        """))


def test_missing_start_url(tmp_path):
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, """
            site:
              name: Demo
            selectors:
              product: .p
              name: .n
              price: .price
        """))


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "does-not-exist.yaml")


def test_invalid_yaml(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("site: [unclosed", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path)
