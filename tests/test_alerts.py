import json

import pytest

from price_scraper.alerts import detect_alerts, load_rules, write_alerts
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


@pytest.mark.parametrize("rules", [
    [], {"unknown": 1}, {"default": []}, {"products": []},
    {"products": {"url:absent": False}}, {"default": {"max_prcie": 10}},
    {"default": {"max_price": float("nan")}}, {"default": {"max_price": "Infinity"}},
    {"default": {"max_price": True}}, {"default": {"max_price": -1}},
    {"default": {"max_price": 10**1000}}, {"default": {"min_drop_percent": 101}},
    {"products": {"url:absent": {"min_drop_percent": "bad"}}},
])
def test_complete_policy_is_validated_even_when_no_products_changed(rules):
    with pytest.raises(ValueError):
        detect_alerts([], rules)


def test_null_threshold_disables_default_and_bom_policy_is_supported(tmp_path):
    path = tmp_path / "rules.json"
    path.write_text(json.dumps({
        "default": {"max_price": 50}, "products": {"url:a": {"max_price": None}}
    }), encoding="utf-8-sig")
    rules = load_rules(path)
    assert detect_alerts([Change("added", "url:a", "A", new_price=20)], rules) == []
    alerts = detect_alerts([Change("added", "url:b", "B", new_price=20)], rules)
    output = write_alerts(alerts, tmp_path / "alerts.json")
    assert json.loads(output.read_text(encoding="utf-8"))["alert_count"] == 1


def test_invalid_json_numbers_are_never_written(tmp_path):
    output = tmp_path / "alerts.json"
    with pytest.raises(ValueError):
        write_alerts([{"new_price": float("nan")}], output)
    assert not output.exists()
