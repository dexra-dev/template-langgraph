"""A pre-buy gate to copy into a Python bot you already have.

It uses only the standard library, not this template's core: call it right before your swap and buy
only when `allowed` is true.

    DEXRA_API_KEY=dxr_... python examples/whitelist_gate.py <mint> [sol]
"""

from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

DEFAULT_BASE_URL = "https://api.dexra.dev"


@dataclass
class GateResult:
    """Whether the buy may go ahead, the Dexra verdicts behind it, and why."""

    allowed: bool = True
    # False when the gate is off (no key).
    checked: bool = False
    verdicts: dict[str, str | None] = field(default_factory=lambda: {"token": None, "preflight": None})
    reasons: list[str] = field(default_factory=list)


def _call(base_url: str, api_key: str, path: str, params: dict[str, Any], timeout: float) -> dict[str, Any]:
    request = Request(
        f"{base_url.rstrip('/')}{path}?{urlencode(params)}",
        headers={"accept": "application/json", "authorization": f"Bearer {api_key}"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            data = json.load(response)
    except HTTPError as error:
        try:
            kind = json.load(error).get("error")
        except (ValueError, AttributeError):
            kind = None
        raise RuntimeError(f"{kind or 'error'} (HTTP {error.code})") from error
    result = data.get("result") if isinstance(data, dict) else None
    return result if isinstance(result, dict) else {}


def dexra_allows_buy(
    mint: str,
    amount_sol: float,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    allow_caution: bool = False,
    fail_open: bool = False,
    timeout: float = 10.0,
) -> GateResult:
    """Whitelist check: the buy passes only if token_deep_check and swap_preflight both say `ok`.

    Without a key the gate is off and allows every buy, so adding it changes nothing until you opt in.
    On a Dexra error (402, 429, 5xx, network) it blocks the buy, or allows it with fail_open=True.
    """
    api_key = api_key or os.environ.get("DEXRA_API_KEY")
    result = GateResult()
    if not api_key:
        result.reasons.append("DEXRA_API_KEY is not set, gate is off")
        return result

    base_url = base_url or os.environ.get("DEXRA_BASE_URL") or DEFAULT_BASE_URL
    checks: list[tuple[str, str, dict[str, Any]]] = [
        ("token_deep_check", "token", {"mint": mint}),
        ("swap_preflight", "preflight", {"mint": mint, "side": "buy", "amount": amount_sol}),
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_call, base_url, api_key, f"/{tool}", params, timeout) for tool, _, params in checks]
    result.checked = True

    for (tool, key, _), future in zip(checks, futures, strict=True):
        try:
            answer = future.result()
        except Exception as error:  # any failure to get a verdict is a Dexra error
            if not fail_open:
                result.allowed = False
            result.reasons.append(f"{tool} failed ({error}), {'allowing' if fail_open else 'blocking'}")
            continue
        verdict = answer.get("verdict")
        verdict = verdict if verdict in ("ok", "caution", "avoid") else "unknown"
        result.verdicts[key] = verdict
        if not (verdict == "ok" or (verdict == "caution" and allow_caution)):
            result.allowed = False
        result.reasons.append(f"{tool}: {verdict}")
        result.reasons.extend(f"{tool}: {r}" for r in answer.get("reasons") or [])
    return result


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: python examples/whitelist_gate.py <mint> [sol]")
    sol = float(sys.argv[2]) if len(sys.argv) > 2 else 0.01
    print(json.dumps(asdict(dexra_allows_buy(sys.argv[1], sol)), indent=2))
