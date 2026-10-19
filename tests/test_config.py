import json
from dataclasses import asdict

import pytest

from dexra_core.config import ConfigError, load_config


def test_defaults_are_a_safe_dry_run():
    c = load_config({})
    assert c.dexra_base_url == "https://api.dexra.dev"
    assert not c.live and c.dry_run_requested
    assert (c.max_trade_sol, c.slippage_bps, c.allow_caution) == (0.05, 100, False)


def test_live_needs_all_three():
    full = {"DRY_RUN": "false", "SOLANA_PRIVATE_KEY": "secret", "JUPITER_API_KEY": "jup"}
    assert load_config(full).live
    assert not load_config({**full, "DRY_RUN": "true"}).live
    assert load_config({**full, "SOLANA_PRIVATE_KEY": ""}).dry_run_reason == "SOLANA_PRIVATE_KEY is not set"
    assert load_config({**full, "JUPITER_API_KEY": ""}).dry_run_reason == "JUPITER_API_KEY is not set"


@pytest.mark.parametrize(
    "env",
    [{"SLIPPAGE_BPS": "1001"}, {"SLIPPAGE_BPS": "1.5"}, {"MAX_TRADE_SOL": "0"}, {"MAX_TRADE_SOL": "nan"}, {"DRY_RUN": "maybe"}],
)
def test_invalid_limits_are_refused(env):
    with pytest.raises(ConfigError):
        load_config(env)


def test_secret_never_shows():
    c = load_config({"SOLANA_PRIVATE_KEY": "super-secret-key"})
    assert c.secret_key() == "super-secret-key"
    assert "super-secret-key" not in repr(c)
    assert "super-secret-key" not in json.dumps(asdict(c), default=lambda _: "<fn>")
