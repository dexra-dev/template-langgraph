"""LangChain tools built on dexra_core. buy and sell always go through the core guard."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, tool

from dexra_core import trade
from dexra_core.config import Config, load_config
from dexra_core.guard import dexra_for


def _call(config: Config | None, method: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
    try:
        client = dexra_for(config or load_config())
    except Exception as error:  # noqa: BLE001 - the model gets an error it can read
        return {"error": str(error)}
    try:
        return getattr(client, method)(*args, **kwargs) or {}
    except Exception as error:  # noqa: BLE001
        return {"error": str(error)}
    finally:
        client.close()


def _launch_brief(launch: dict[str, Any]) -> dict[str, Any]:
    token = launch.get("token") or {}
    return {
        "mint": launch.get("mint"),
        "symbol": token.get("symbol"),
        "name": token.get("name"),
        "platform": launch.get("platform"),
        "age_secs": launch.get("age_secs"),
        "liquidity": launch.get("liquidity"),
        "verdict": launch.get("verdict"),
        "reasons": launch.get("reasons"),
    }


def check_token_result(mint: str, config: Config | None = None) -> dict[str, Any]:
    return _call(config, "token_deep_check", mint)


def preflight_result(mint: str, side: str, amount: float, config: Config | None = None) -> dict[str, Any]:
    return _call(config, "swap_preflight", mint, side, amount)


def fresh_launches_result(
    minutes: int = 30,
    limit: int = 10,
    min_liquidity_usd: float | None = None,
    config: Config | None = None,
) -> dict[str, Any]:
    result = _call(config, "new_launches", minutes, limit, min_liquidity_usd)
    if "error" in result:
        return result
    # Full launch records are large; the model only needs enough to pick one to check.
    return {
        "summary": result.get("summary"),
        "count": result.get("count"),
        "launches": [_launch_brief(x) for x in result.get("launches") or []],
    }


def make_tools(config: Config | None = None) -> list[BaseTool]:
    """The five trading tools. Pass a config to pin settings; by default each call reads the environment."""

    @tool
    def check_token(mint: str) -> dict[str, Any]:
        """Dexra token_deep_check: holder graph, mint and freeze authorities, Token-2022 extensions.
        Returns verdict (ok, caution, avoid), reasons and summary."""
        return check_token_result(mint, config)

    @tool
    def preflight(mint: str, side: str = "buy", amount: float = 0.01) -> dict[str, Any]:
        """Dexra swap_preflight: price impact, sandwich risk and token risks before a swap.
        side is buy or sell. For a buy, amount is in SOL; for a sell, in token units."""
        return preflight_result(mint, side, amount, config)

    @tool
    def fresh_launches(minutes: int = 30, limit: int = 10, min_liquidity_usd: float | None = None) -> dict:
        """Dexra new_launches: tokens launched in the last N minutes, each with its own verdict."""
        return fresh_launches_result(minutes, limit, min_liquidity_usd, config)

    @tool
    def buy(mint: str, amount_sol: float) -> dict[str, Any]:
        """Buy a token with SOL. Runs token_deep_check and swap_preflight first and refuses on a bad
        verdict or any Dexra error. Dry run unless live trading is switched on. Never raises."""
        return trade.buy(mint, amount_sol, config)

    @tool
    def sell(mint: str, amount_atomic: int) -> dict[str, Any]:
        """Sell a token for SOL. amount_atomic is in the token's smallest units. Never blocked by a
        verdict, so a position can always be exited. Dry run unless live trading is on."""
        return trade.sell(mint, amount_atomic, config)

    return [check_token, preflight, fresh_launches, buy, sell]
