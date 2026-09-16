"""Apply explicit price alert rules to PriceWatch changes."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .history import Change


def load_rules(path: str | Path) -> dict:
    """Load a JSON alert policy and validate its top-level shape."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Alert rules must be a JSON object.")
    products = payload.get("products", {})
    if products is not None and not isinstance(products, dict):
        raise ValueError("The products alert rules must be an object.")
    return payload


def _merged_rule(rules: dict, product_key: str) -> dict:
    default = rules.get("default") or {}
    specific = (rules.get("products") or {}).get(product_key) or {}
    if not isinstance(default, dict) or not isinstance(specific, dict):
        raise ValueError("Each alert rule must be an object.")
    return {**default, **specific}


def _number(rule: dict, field: str) -> float | None:
    value = rule.get(field)
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric.") from exc
    if result < 0:
        raise ValueError(f"{field} must not be negative.")
    return result


def detect_alerts(changes: Iterable[Change], rules: dict) -> list[dict]:
    """Return only changes that satisfy a max-price or drop-percent rule."""
    alerts: list[dict] = []
    for change in changes:
        if change.kind not in {"added", "price_changed"}:
            continue
        price = change.new_price
        if price is None:
            continue

        rule = _merged_rule(rules, change.product_key)
        max_price = _number(rule, "max_price")
        min_drop = _number(rule, "min_drop_percent")
        reasons: list[str] = []

        if max_price is not None and price <= max_price:
            reasons.append(f"price {price:.2f} <= threshold {max_price:.2f}")

        drop_percent = None
        if (
            min_drop is not None
            and change.old_price is not None
            and change.old_price > 0
            and change.kind == "price_changed"
        ):
            drop_percent = (change.old_price - price) / change.old_price * 100
            if drop_percent >= min_drop:
                reasons.append(
                    f"drop {drop_percent:.2f}% >= threshold {min_drop:.2f}%"
                )

        if reasons:
            alerts.append(
                {
                    "kind": change.kind,
                    "product_key": change.product_key,
                    "name": change.name,
                    "url": change.url,
                    "old_price": change.old_price,
                    "new_price": price,
                    "drop_percent": round(drop_percent, 2)
                    if drop_percent is not None
                    else None,
                    "reasons": reasons,
                }
            )
    return alerts


def write_alerts(alerts: list[dict], target: str | Path) -> Path:
    """Write an audit-friendly JSON alert result."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "alert_count": len(alerts),
        "alerts": alerts,
    }
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return target
