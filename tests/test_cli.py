from pathlib import Path

import pytest

from price_scraper import cli
from price_scraper.config import Config, Selectors
from price_scraper.scraper import Product


def config(tmp_path):
    return Config(name="offline", start_url="https://example.test", base_url="https://example.test",
                  selectors=Selectors(product=".p", name=".name", price=".price"),
                  csv_path=str(tmp_path / "prices.csv"))


@pytest.mark.parametrize("policy", ["{", '{"default":{"max_price":"NaN"}}',
                                     '{"products":{"url:unused":{"min_drop_percent":150}}}'])
def test_invalid_alert_policy_fails_before_network_or_output(policy, tmp_path, monkeypatch, capsys):
    cfg = config(tmp_path)
    monkeypatch.setattr(cli, "load_config", lambda _: cfg)

    def unexpected_scrape(_):
        pytest.fail("Policy validation must happen before any network request")

    monkeypatch.setattr(cli, "scrape", unexpected_scrape)
    rules = tmp_path / "rules.json"
    rules.write_text(policy, encoding="utf-8")
    csv = Path(cfg.csv_path)
    csv.write_text("existing output", encoding="utf-8")
    database = tmp_path / "history.db"
    assert cli.main(["--database", str(database), "--alert-rules", str(rules)]) == 2
    assert csv.read_text(encoding="utf-8") == "existing output"
    assert not database.exists()
    assert "Config error:" in capsys.readouterr().err


def test_output_errors_return_a_clean_exit_code(tmp_path, monkeypatch, capsys):
    cfg = config(tmp_path)
    cfg.csv_path = str(tmp_path)
    monkeypatch.setattr(cli, "load_config", lambda _: cfg)
    monkeypatch.setattr(cli, "scrape", lambda _: [Product("A", 1, "USD", None, "https://x/a")])
    assert cli.main(["--quiet"]) == 1
    assert "CSV output failed:" in capsys.readouterr().err


@pytest.mark.parametrize("args", [["--max-pages", "0"], ["--max-pages", "-1"],
                                 ["--alert-output", "unused.json"]])
def test_invalid_cli_combinations_fail_before_config_is_read(args, monkeypatch):
    def unexpected_config(_):
        pytest.fail("Invalid CLI arguments must be rejected before loading a configuration")

    monkeypatch.setattr(cli, "load_config", unexpected_config)
    assert cli.main(args) == 2
