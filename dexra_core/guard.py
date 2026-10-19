"""The rules that decide whether a trade may go ahead."""

from __future__ import annotations

import math
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from .config import Config, load_config
from .dexra_client import DexraClient

MINT_PATTERN = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


@dataclass
class Decision:
    """Whether a trade is allowed, the Dexra verdicts behind it, and why."""

    side: Literal["buy", "sell"]
    mint: str
    amount: float  # SOL for a buy, token units for a sell
    allowed: bool = False
    verdicts: dict[str, str | None] = field(
        default_factory=lambda: {"token": None, "preflight": None}
    )
    reasons: list[str] = field(default_factory=list)
    summaries: dict[str, str | None] = field(
        default_factory=lambda: {"token": None, "preflight": None}
    )

    def to_dict(self) -> dict[str, Any]:
        """Plain JSON-serializable form."""
        return asdict(self)


def dexra_for(config: Config) -> DexraClient:
    """A Dexra client built from the config."""
    return DexraClient(api_key=config.dexra_api_key, base_url=config.dexra_base_url)


def _verdict(result: Any) -> str:
    v = result.get("verdict") if isinstance(result, dict) else None
    return v if v in ("ok", "caution", "avoid") else "unknown"


def _reasons(tool: str, result: Any) -> list[str]:
    reasons = result.get("reasons") if isinstance(result, dict) else None
    return [f"{tool}: {r}" for r in reasons or []]


def _summary(result: Any) -> str | None:
    s = result.get("summary") if isinstance(result, dict) else None
    return s if isinstance(s, str) else None


def _valid_amount(amount: Any) -> bool:
    return isinstance(amount, (int, float)) and math.isfinite(amount) and amount > 0


def decide_buy(mint: str, amount_sol: float, config: Config | None = None) -> Decision:
    """Decides whether buying amount_sol SOL of mint is allowed; fails closed on any Dexra error."""
    config = config or load_config()
    decision = Decision(side="buy", mint=mint, amount=amount_sol)

    if not MINT_PATTERN.match(mint or ""):
        decision.reasons.append("mint is not a valid Solana address")
        return decision
    if not _valid_amount(amount_sol):
        decision.reasons.append("amount must be above 0 SOL")
        return decision
    if amount_sol > config.max_trade_sol:
        decision.reasons.append(
            f"amount {amount_sol} SOL is above MAX_TRADE_SOL {config.max_trade_sol}"
        )
        return decision

    client = dexra_for(config)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {
                ("token_deep_check", "token"): pool.submit(client.token_deep_check, mint),
                ("swap_preflight", "preflight"): pool.submit(
                    client.swap_preflight, mint, "buy", amount_sol
                ),
            }
            outcomes: dict[tuple[str, str], tuple[dict[str, Any] | None, Exception | None]] = {}
            for names, future in futures.items():
                try:
                    outcomes[names] = (future.result(), None)
                except Exception as exc:  # noqa: BLE001 - every failure must block
                    outcomes[names] = (None, exc)
    finally:
        client.close()

    blockers: list[str] = []
    notes: list[str] = []
    for (tool, key), (result, failure) in outcomes.items():
        if failure is not None or result is None:
            blockers.append(f"{tool} failed, blocking to be safe: {failure}")
            continue
        verdict = _verdict(result)
        decision.verdicts[key] = verdict
        decision.summaries[key] = _summary(result)
        if verdict == "ok" or (verdict == "caution" and config.allow_caution):
            notes += [f"{tool} verdict {verdict}", *_reasons(tool, result)]
        else:
            hint = " (set ALLOW_CAUTION=true to accept caution)" if verdict == "caution" else ""
            blockers += [f"{tool} verdict {verdict} blocks the buy{hint}", *_reasons(tool, result)]

    decision.allowed = not blockers
    decision.reasons = notes if decision.allowed else blockers
    return decision


def decide_sell(mint: str, amount_tokens: float | None, config: Config | None = None) -> Decision:
    """Reports the preflight verdict for a sell (None amount: no preflight); never blocks a valid exit."""
    config = config or load_config()
    decision = Decision(side="sell", mint=mint, amount=amount_tokens or 0)

    if not MINT_PATTERN.match(mint or ""):
        decision.reasons.append("mint is not a valid Solana address")
        return decision
    if amount_tokens is None:
        decision.allowed = True
        decision.reasons.append(
            "token amount unknown (mint decimals unavailable), so no preflight was run; selling anyway"
        )
        return decision
    if not _valid_amount(amount_tokens):
        decision.reasons.append("amount must be above 0 tokens")
        return decision

    decision.allowed = True
    client = dexra_for(config)
    try:
        result = client.swap_preflight(mint, "sell", amount_tokens)
        decision.verdicts["preflight"] = _verdict(result)
        decision.summaries["preflight"] = _summary(result)
        decision.reasons += [
            f"swap_preflight verdict {decision.verdicts['preflight']}",
            *_reasons("swap_preflight", result),
        ]
    except Exception as error:  # noqa: BLE001 - a Dexra outage must not trap a position
        decision.reasons.append(f"swap_preflight failed, selling anyway: {error}")
    finally:
        client.close()
    return decision
