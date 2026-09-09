"""Fetch listing pages, parse them with BeautifulSoup, and extract products."""
from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .config import Config


@dataclass
class Product:
    name: str
    price: float | None
    currency: str
    availability: str | None
    url: str | None

    def as_row(self) -> dict:
        return asdict(self)


def parse_price(text: str | None) -> float | None:
    """Turn a price string into a float.

    Handles symbols and thousands separators, e.g. ``"£51.77"`` -> ``51.77``
    and ``"$1,234.50"`` -> ``1234.50``. The right-most ``.``/``,`` followed by
    1-2 digits is treated as the decimal separator. Returns ``None`` when no
    number is present.
    """
    if not text:
        return None
    s = re.sub(r"[^\d.,]", "", str(text))
    if not re.search(r"\d", s):
        return None

    last_sep = max(s.rfind("."), s.rfind(","))
    after = s[last_sep + 1:] if last_sep != -1 else ""
    if last_sep != -1 and 1 <= len(after) <= 2:
        integer = re.sub(r"[.,]", "", s[:last_sep])
        s = f"{integer}.{after}"
    else:
        s = re.sub(r"[.,]", "", s)  # all separators are thousands groupings

    try:
        return float(s)
    except ValueError:
        return None


def _extract(node, selector: str | None, attr: str | None) -> str | None:
    if not selector:
        return None
    el = node.select_one(selector)
    if el is None:
        return None
    if attr:
        val = el.get(attr)
        return val.strip() if isinstance(val, str) else None
    return el.get_text(strip=True)


def extract_products(html: str, config: Config) -> list[Product]:
    """Parse one page of HTML into a list of :class:`Product`."""
    soup = BeautifulSoup(html, "lxml")
    sel = config.selectors
    products: list[Product] = []
    for node in soup.select(sel.product):
        url = _extract(node, sel.url, sel.url_attr) if sel.url else None
        if url:
            url = urljoin(config.base_url, url)
        availability = _extract(node, sel.availability, None) if sel.availability else None
        products.append(
            Product(
                name=_extract(node, sel.name, sel.name_attr) or "",
                price=parse_price(_extract(node, sel.price, None)),
                currency=config.currency_symbol,
                availability=availability,
                url=url,
            )
        )
    return products


def find_next_url(html: str, config: Config, current_url: str) -> str | None:
    """Resolve the absolute URL of the next page, or ``None`` if there isn't one."""
    if not config.pagination.next:
        return None
    soup = BeautifulSoup(html, "lxml")
    el = soup.select_one(config.pagination.next)
    if el is None:
        return None
    href = el.get(config.pagination.next_attr)
    if not href:
        return None
    return urljoin(current_url, href)


def scrape(config: Config, session: requests.Session | None = None) -> list[Product]:
    """Scrape products across pages, following pagination up to ``max_pages``.

    A polite ``delay_seconds`` pause is inserted between requests. Pass a
    ``session`` to reuse a connection (and to inject a fake one in tests).
    """
    own_session = session is None
    session = session or requests.Session()
    session.headers.setdefault("User-Agent", config.request.user_agent)

    products: list[Product] = []
    url: str | None = config.start_url
    pages = 0
    try:
        while url and pages < config.request.max_pages:
            resp = session.get(url, timeout=config.request.timeout)
            resp.raise_for_status()
            html = resp.text
            products.extend(extract_products(html, config))
            pages += 1
            next_url = find_next_url(html, config, url)
            if next_url and pages < config.request.max_pages:
                time.sleep(config.request.delay_seconds)
            url = next_url
    finally:
        if own_session:
            session.close()
    return products
