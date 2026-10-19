"""Settings from the environment, with the safety limits the guard enforces."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

SOL_MINT = "So11111111111111111111111111111111111111112"
LAMPORTS_PER_SOL = 1_000_000_000
SLIPPAGE_BPS_CAP = 1000


class ConfigError(ValueError):
    """An environment value is missing or out of range."""


@dataclass(frozen=True)
class Config:
    """The core's settings; `live` is True only when trades are really sent."""

    dexra_api_key: str | None
    dexra_base_url: str
    jupiter_api_key: str | None
    # A function, so printing or serializing the config never shows the wallet secret.
    secret_key: Callable[[], str | None] = field(repr=False, compare=False)
    has_wallet: bool
    dry_run_requested: bool
    live: bool
    dry_run_reason: str | None
    max_trade_sol: float
    slippage_bps: int
    allow_caution: bool


def _text(env: Mapping[str, str], name: str) -> str | None:
    value = (env.get(name) or "").strip()
    return value or None


def _flag(env: Mapping[str, str], name: str, default: bool) -> bool:
    value = _text(env, name)
    if value is None:
        return default
    value = value.lower()
    if value in ("true", "1", "yes", "on"):
        return True
    if value in ("false", "0", "no", "off"):
        return False
    raise ConfigError(f'{name} must be true or false, got "{value}"')


def _number(env: Mapping[str, str], name: str, default: float) -> float:
    value = _text(env, name)
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError:
        raise ConfigError(f'{name} must be a number, got "{value}"') from None
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        raise ConfigError(f'{name} must be a number, got "{value}"')
    return parsed


def load_config(env: Mapping[str, str] | None = None) -> Config:
    """Reads settings from the environment; raises ConfigError on invalid values."""
    env = os.environ if env is None else env

    max_trade_sol = _number(env, "MAX_TRADE_SOL", 0.05)
    if max_trade_sol <= 0:
        raise ConfigError("MAX_TRADE_SOL must be above 0")

    slippage = _number(env, "SLIPPAGE_BPS", 100)
    if slippage != int(slippage) or not 0 <= slippage <= SLIPPAGE_BPS_CAP:
        raise ConfigError(f"SLIPPAGE_BPS must be a whole number from 0 to {SLIPPAGE_BPS_CAP}")

    secret = _text(env, "SOLANA_PRIVATE_KEY")
    jupiter_api_key = _text(env, "JUPITER_API_KEY")
    dry_run_requested = _flag(env, "DRY_RUN", True)

    reason: str | None = None
    if dry_run_requested:
        reason = "DRY_RUN is not false"
    elif not secret:
        reason = "SOLANA_PRIVATE_KEY is not set"
    elif not jupiter_api_key:
        reason = "JUPITER_API_KEY is not set"

    return Config(
        dexra_api_key=_text(env, "DEXRA_API_KEY"),
        dexra_base_url=(_text(env, "DEXRA_BASE_URL") or "https://api.dexra.dev").rstrip("/"),
        jupiter_api_key=jupiter_api_key,
        secret_key=lambda: secret,
        has_wallet=secret is not None,
        dry_run_requested=dry_run_requested,
        live=reason is None,
        dry_run_reason=reason,
        max_trade_sol=max_trade_sol,
        slippage_bps=int(slippage),
        allow_caution=_flag(env, "ALLOW_CAUTION", False),
    )
