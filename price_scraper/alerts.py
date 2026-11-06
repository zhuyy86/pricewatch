"""Apply explicit price alert rules to PriceWatch changes."""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .history import Change


def load_rules(path: str | Path) -> dict:
    """Validate the entire policy before scraping or committing a snapshot."""
    return validate_rules(json.loads(Path(path).read_text(encoding="utf-8-sig")))


def validate_rules(payload: dict) -> dict:
    if not isinstance(payload, dict) or set(payload) - {"default", "products"}:
        raise ValueError("Alert rules must be a JSON object.")
    default = payload.get("default")
    products = payload.get("products")
    default = {} if default is None else default
    products = {} if products is None else products
    if not isinstance(default, dict) or not isinstance(products, dict):
        raise ValueError("The products alert rules must be an object.")
    for rule in [default, *products.values()]:
        if not isinstance(rule, dict) or set(rule) - {"max_price", "min_drop_percent"}:
            raise ValueError("Each alert rule must contain only max_price and min_drop_percent.")
        _number(rule, "max_price")
        _number(rule, "min_drop_percent")
    if any(not isinstance(key, str) or not key.strip() for key in products):
        raise ValueError("Product alert keys must be non-empty strings.")
    return {"default": default, "products": products}


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
    if isinstance(value, bool):
        raise ValueError(f"{field} must be numeric, not boolean.")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{field} must be numeric.") from exc
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{field} must be finite and non-negative.")
    if field == "min_drop_percent" and result > 100:
        raise ValueError("min_drop_percent must not exceed 100.")
    return result


def detect_alerts(changes: Iterable[Change], rules: dict) -> list[dict]:
    """Return only changes that satisfy a max-price or drop-percent rule."""
    rules = validate_rules(rules)
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


def render_alerts(alerts: list[dict], generated_at: str | None = None) -> str:
    payload = {
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "alert_count": len(alerts),
        "alerts": alerts,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def write_alerts(alerts: list[dict], target: str | Path) -> Path:
    """Write an audit-friendly JSON alert result."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_alerts(alerts), encoding="utf-8")
    return target
