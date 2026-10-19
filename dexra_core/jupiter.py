"""Jupiter Swap API v2, Meta-Aggregator path: https://developers.jup.ag/docs/swap/order-and-execute"""

from __future__ import annotations

from typing import Any

import httpx

from .config import Config, load_config

JUPITER_BASE_URL = "https://api.jup.ag/swap/v2"


class JupiterError(Exception):
    """A failed Jupiter call, with the HTTP status and the parsed body."""

    def __init__(self, status: int, message: str, body: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.body = body


def _key(config: Config) -> str:
    if not config.jupiter_api_key:
        raise JupiterError(0, "JUPITER_API_KEY is not set")
    return config.jupiter_api_key


def _parse(response: httpx.Response, what: str) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        body = response.text
    if response.is_error:
        detail = body.get("error") if isinstance(body, dict) else str(body)[:200]
        raise JupiterError(
            response.status_code, f"{what} failed with HTTP {response.status_code}: {detail}", body
        )
    return body


def order(
    input_mint: str,
    output_mint: str,
    amount: int,
    taker: str | None = None,
    slippage_bps: int | None = None,
    config: Config | None = None,
) -> dict[str, Any]:
    """GET /order: a quote, plus an unsigned base64 transaction when taker is given."""
    config = config or load_config()
    params: dict[str, Any] = {"inputMint": input_mint, "outputMint": output_mint, "amount": str(amount)}
    if taker:
        params["taker"] = taker
    if slippage_bps is not None:
        params["slippageBps"] = slippage_bps
    response = httpx.get(
        f"{JUPITER_BASE_URL}/order",
        params=params,
        headers={"x-api-key": _key(config), "accept": "application/json"},
        timeout=20.0,
    )
    return _parse(response, "Jupiter /order")


def execute(signed_transaction: str, request_id: str, config: Config | None = None) -> dict[str, Any]:
    """POST /execute: lands a signed /order transaction; returns status, signature and code."""
    config = config or load_config()
    response = httpx.post(
        f"{JUPITER_BASE_URL}/execute",
        json={"signedTransaction": signed_transaction, "requestId": request_id},
        headers={"x-api-key": _key(config), "accept": "application/json"},
        timeout=60.0,
    )
    return _parse(response, "Jupiter /execute")
