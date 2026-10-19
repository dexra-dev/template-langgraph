"""A small Dexra API client (the Python SDK is not on PyPI yet)."""

from __future__ import annotations

from typing import Any

import httpx

DEFAULT_BASE_URL = "https://api.dexra.dev"


class DexraError(Exception):
    """A failed Dexra call: HTTP status, the API's error kind and its message."""

    def __init__(self, status: int, kind: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.kind = kind
        self.message = message

    def __str__(self) -> str:
        return f"{self.kind} (HTTP {self.status}): {self.message}"


class DexraClient:
    """Calls Dexra tools and returns the `result` of each answer."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """Creates a client; without api_key paid tools answer 402."""
        headers = {"accept": "application/json"}
        if api_key:
            headers["authorization"] = f"Bearer {api_key}"
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"), headers=headers, timeout=timeout, transport=transport
        )

    def request(
        self, method: str, path: str, params: dict[str, Any] | None = None, body: Any = None
    ) -> Any:
        """Calls any endpoint; raises DexraError on a non-2xx answer."""
        query = {k: v for k, v in (params or {}).items() if v is not None}
        response = self._http.request(method, path, params=query, json=body)
        try:
            data = response.json() if response.content else {}
        except ValueError:
            data = {}
        if response.is_error:
            default_kind = "payment_required" if response.status_code == 402 else "error"
            raise DexraError(
                response.status_code,
                str(data.get("error", default_kind)) if isinstance(data, dict) else default_kind,
                str(data.get("context", f"HTTP {response.status_code}"))
                if isinstance(data, dict)
                else f"HTTP {response.status_code}",
            )
        return data.get("result") if isinstance(data, dict) else None

    def token_deep_check(self, mint: str) -> dict[str, Any]:
        """Holder graph, authorities and Token-2022 extensions for a mint, with a verdict."""
        return self.request("GET", "/token_deep_check", {"mint": mint})

    def swap_preflight(
        self, mint: str, side: str, amount: float, quote: str | None = None
    ) -> dict[str, Any]:
        """Price impact, sandwich risk and token risks before a swap; amount is quote units for buy."""
        return self.request(
            "GET", "/swap_preflight", {"mint": mint, "side": side, "amount": amount, "quote": quote}
        )

    def new_launches(
        self,
        minutes: int | None = None,
        limit: int | None = None,
        min_liquidity_usd: float | None = None,
        platforms: str | None = None,
    ) -> dict[str, Any]:
        """Fresh launches from the last N minutes, each with its own verdict."""
        params = {
            "minutes": minutes,
            "limit": limit,
            "min_liquidity_usd": min_liquidity_usd,
            "platforms": platforms,
        }
        return self.request("GET", "/new_launches", params)

    def describe_address(self, address: str) -> dict[str, Any]:
        """What any address is (free)."""
        return self.request("GET", "/describe_address", {"address": address})

    def close(self) -> None:
        """Closes the HTTP connection pool."""
        self._http.close()
