"""Local, dependency-free HTML report for observed changes."""
from dataclasses import asdict
from html import escape
from pathlib import Path


LABELS = {
    "added": "New product",
    "removed": "Missing from latest snapshot",
    "price_changed": "Price changed",
    "availability_changed": "Availability changed",
}


def write_html(changes, target, title="PriceWatch snapshot changes"):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for change in changes:
        item = asdict(change)
        before = item["old_price"] if item["kind"] == "price_changed" else item["old_availability"]
        after = item["new_price"] if item["kind"] == "price_changed" else item["new_availability"]
        link = f'<a href="{escape(item["url"], quote=True)}">open</a>' if item["url"] else ""
        rows.append("<tr>" + "".join(
            f"<td>{escape(str(value if value is not None else '—'))}</td>"
            for value in (LABELS[item["kind"]], item["name"], before, after)
        ) + f"<td>{link}</td></tr>")
    body = "\n".join(rows) or '<tr><td colspan="5">No changes detected.</td></tr>'
    html = f"""<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title>
<style>body{{font:16px system-ui;max-width:1100px;margin:3rem auto;padding:0 1rem;color:#172033}}
h1{{font-size:2rem}}.meta{{color:#667085}}table{{border-collapse:collapse;width:100%;margin-top:2rem}}
th,td{{padding:.75rem;border-bottom:1px solid #ddd;text-align:left}}th{{background:#f5f7fa}}
a{{color:#175cd3}}</style><h1>{escape(title)}</h1>
<p class="meta">{len(changes)} observed change event(s). A missing item means it was absent from
the latest successful snapshot; it does not prove the product was discontinued.</p>
<table><thead><tr><th>Change</th><th>Product</th><th>Before</th><th>After</th><th>Link</th></tr>
</thead><tbody>{body}</tbody></table></html>"""
    target.write_text(html, encoding="utf-8")
    return target
