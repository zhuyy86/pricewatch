from pathlib import Path

import pytest

from price_scraper.config import Config, Pagination, RequestSettings, Selectors
from price_scraper.scraper import extract_products, find_next_url, parse_price, scrape

FIXTURE = Path(__file__).parent / "fixtures" / "books_listing.html"


@pytest.fixture
def html():
    return FIXTURE.read_text(encoding="utf-8")


@pytest.fixture
def demo_config():
    return Config(
        name="demo",
        start_url="https://books.toscrape.com/catalogue/page-1.html",
        base_url="https://books.toscrape.com/catalogue/",
        selectors=Selectors(
            product="article.product_pod",
            name="h3 a",
            name_attr="title",
            price="p.price_color",
            availability="p.instock.availability",
            url="h3 a",
            url_attr="href",
        ),
        pagination=Pagination(next="li.next a", next_attr="href"),
        request=RequestSettings(max_pages=3, delay_seconds=0),
        currency_symbol="£",
    )


@pytest.mark.parametrize(
    "text,expected",
    [
        ("£51.77", 51.77),
        ("$1,234.50", 1234.50),
        ("1.234,50", 1234.50),    # European format -> rightmost separator is decimal
        ("1,000", 1000.0),        # thousands separator, no decimals
        ("42", 42.0),
        ("Price: 9.99 USD", 9.99),
        ("", None),
        (None, None),
        ("out of stock", None),
    ],
)
def test_parse_price(text, expected):
    assert parse_price(text) == expected


def test_extract_products(html, demo_config):
    products = extract_products(html, demo_config)
    assert len(products) == 2

    first = products[0]
    assert first.name == "A Light in the Attic"
    assert first.price == 51.77
    assert first.currency == "£"
    assert first.availability == "In stock"
    assert first.url == (
        "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html"
    )

    assert products[1].name == "Tipping the Velvet"
    assert products[1].price == 53.74


def test_find_next_url(html, demo_config):
    nxt = find_next_url(html, demo_config, demo_config.start_url)
    assert nxt == "https://books.toscrape.com/catalogue/page-2.html"


def test_no_pagination_returns_none(html, demo_config):
    demo_config.pagination = Pagination(next=None)
    assert find_next_url(html, demo_config, demo_config.start_url) is None


class _FakeResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


class _FakeSession:
    """Serves canned HTML per URL so scrape() can be tested without a network."""

    def __init__(self, pages):
        self.pages = pages
        self.headers: dict = {}
        self.requested: list[str] = []

    def get(self, url, timeout=None):
        self.requested.append(url)
        return _FakeResponse(self.pages[url])

    def close(self):
        return None


def test_scrape_follows_pagination(html, demo_config):
    page2 = """
      <ol><li><article class="product_pod">
        <h3><a href="x_1/index.html" title="Third Book">..</a></h3>
        <p class="price_color">£10.00</p>
        <p class="instock availability">In stock</p>
      </article></li></ol>
    """
    pages = {
        "https://books.toscrape.com/catalogue/page-1.html": html,
        "https://books.toscrape.com/catalogue/page-2.html": page2,
    }
    session = _FakeSession(pages)

    products = scrape(demo_config, session=session)

    assert session.requested == [
        "https://books.toscrape.com/catalogue/page-1.html",
        "https://books.toscrape.com/catalogue/page-2.html",
    ]
    assert len(products) == 3
    assert products[-1].name == "Third Book"
    assert products[-1].price == 10.0


def test_scrape_respects_max_pages(html, demo_config):
    demo_config.request.max_pages = 1
    pages = {"https://books.toscrape.com/catalogue/page-1.html": html}
    session = _FakeSession(pages)

    products = scrape(demo_config, session=session)

    assert session.requested == ["https://books.toscrape.com/catalogue/page-1.html"]
    assert len(products) == 2  # only page 1 scraped
