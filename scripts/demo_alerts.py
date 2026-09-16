"""Run the PriceWatch alert engine without a network request."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from price_scraper.alerts import detect_alerts, write_alerts
from price_scraper.history import Change


def main() -> None:
    changes = [
        Change(
            "price_changed",
            "url:https://example.test/keyboard",
            "Compact keyboard",
            old_price=100.0,
            new_price=84.0,
            url="https://example.test/keyboard",
        ),
        Change(
            "price_changed",
            "url:https://example.test/mouse",
            "Wireless mouse",
            old_price=30.0,
            new_price=29.5,
            url="https://example.test/mouse",
        ),
    ]
    rules = {"default": {"min_drop_percent": 10}}
    alerts = detect_alerts(changes, rules)
    target = write_alerts(alerts, "outputs/demo-alerts.json")
    print(f"Wrote {len(alerts)} alert(s) to {target}")


if __name__ == "__main__":
    main()
