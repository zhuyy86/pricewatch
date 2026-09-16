from price_scraper.alerts import detect_alerts
from price_scraper.history import Change


def test_detects_drop_and_ignores_small_change():
    changes = [
        Change(
            "price_changed", "url:a", "A",
            old_price=100, new_price=85, url="https://example.test/a",
        ),
        Change(
            "price_changed", "url:b", "B",
            old_price=100, new_price=96, url="https://example.test/b",
        ),
    ]

    alerts = detect_alerts(changes, {"default": {"min_drop_percent": 10}})

    assert len(alerts) == 1
    assert alerts[0]["product_key"] == "url:a"
    assert alerts[0]["drop_percent"] == 15.0


def test_specific_max_price_overrides_default():
    change = Change(
        "added", "url:a", "A", new_price=40, url="https://example.test/a"
    )
    rules = {
        "default": {"max_price": 20},
        "products": {"url:a": {"max_price": 50}},
    }

    alerts = detect_alerts([change], rules)

    assert len(alerts) == 1
    assert "threshold 50.00" in alerts[0]["reasons"][0]
