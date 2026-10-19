"""Guarded buy and sell: Dexra decides, Jupiter quotes and executes, dry run by default."""

from __future__ import annotations

import base64
import math
from typing import Any, Literal

from solders.keypair import Keypair
from solders.message import to_bytes_versioned
from solders.transaction import VersionedTransaction

from .config import LAMPORTS_PER_SOL, SOL_MINT, Config, load_config
from .guard import Decision, decide_buy, decide_sell, dexra_for
from .jupiter import execute, order


def load_keypair(config: Config) -> Keypair:
    """Loads the wallet from SOLANA_PRIVATE_KEY (base58, 64 bytes)."""
    secret = config.secret_key()
    if not secret:
        raise ValueError("SOLANA_PRIVATE_KEY is not set")
    try:
        return Keypair.from_base58_string(secret)
    except Exception:
        # Never echo the key or the decoder's message, which can contain it.
        raise ValueError("SOLANA_PRIVATE_KEY is not a valid base58 64-byte secret key") from None


def sign_order_transaction(transaction_b64: str, keypair: Keypair) -> str:
    """Signs a base64 transaction from /order in the wallet's slot and returns it base64 again."""
    tx = VersionedTransaction.from_bytes(base64.b64decode(transaction_b64))
    message = tx.message
    signers = list(message.account_keys)[: message.header.num_required_signatures]
    if keypair.pubkey() not in signers:
        raise ValueError("the order transaction does not need this wallet's signature")
    # Partial: JupiterZ (RFQ) routes leave a market maker slot that /execute fills in.
    signatures = list(tx.signatures)
    signatures[signers.index(keypair.pubkey())] = keypair.sign_message(to_bytes_versioned(message))
    return base64.b64encode(bytes(VersionedTransaction.populate(message, signatures))).decode()


def _quote_summary(o: dict[str, Any]) -> dict[str, Any]:
    return {
        "requestId": o.get("requestId"),
        "inAmount": o.get("inAmount"),
        "outAmount": o.get("outAmount"),
        "minOutAmount": o.get("otherAmountThreshold"),
        "priceImpactPct": o.get("priceImpact"),
        "slippageBps": o.get("slippageBps"),
        "router": o.get("router"),
        "feeBps": o.get("feeBps"),
    }


def _result(
    side: Literal["buy", "sell"],
    mint: str,
    input_mint: str,
    output_mint: str,
    atomic: int,
    amount: float | None,
    decision: Decision | None,
    mode: str,
) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "error",
        "mode": mode,
        "side": side,
        "mint": mint,
        "input": {"mint": input_mint, "amountAtomic": str(atomic), "amount": amount},
        "outputMint": output_mint,
        "decision": decision.to_dict() if decision else None,
        "quote": None,
        "signature": None,
        "explorerUrl": None,
        "execution": None,
        "message": "",
    }


def _run(result: dict[str, Any], decision: Decision, config: Config) -> dict[str, Any]:
    side, input_mint, output_mint = result["side"], result["input"]["mint"], result["outputMint"]
    atomic = int(result["input"]["amountAtomic"])
    if not decision.allowed:
        result["status"] = "blocked"
        first = decision.reasons[0] if decision.reasons else "no reason given"
        result["message"] = f"{side} blocked: {first}"
        return result

    if not config.live:
        result.update(status="dry_run", ok=True)
        if config.jupiter_api_key:
            try:
                quote = order(input_mint, output_mint, atomic, slippage_bps=config.slippage_bps, config=config)
                result["quote"] = _quote_summary(quote)
            except Exception as error:  # noqa: BLE001 - reported, not raised
                result["message"] = (
                    f"would {side}, but the Jupiter quote failed: {error} (dry run: {config.dry_run_reason})"
                )
                return result
        quoted = (
            f" for about {result['quote']['outAmount']} atomic units of {output_mint}"
            if result["quote"]
            else ""
        )
        result["message"] = (
            f"would {side} {atomic} atomic units of {input_mint}{quoted} (dry run: {config.dry_run_reason})"
        )
        return result

    try:
        keypair = load_keypair(config)
        quote = order(
            input_mint,
            output_mint,
            atomic,
            taker=str(keypair.pubkey()),
            slippage_bps=config.slippage_bps,
            config=config,
        )
        result["quote"] = _quote_summary(quote)
        if not quote.get("transaction"):
            result["status"] = "failed"
            result["message"] = (
                f"Jupiter returned no transaction ({quote.get('router', 'router?')} error "
                f"{quote.get('errorCode', '?')}: {quote.get('errorMessage', 'no message')})"
            )
            return result
        signed = sign_order_transaction(quote["transaction"], keypair)
        outcome = execute(signed, quote["requestId"], config=config)
        signature = outcome.get("signature")
        result["signature"] = signature
        result["explorerUrl"] = f"https://solscan.io/tx/{signature}" if signature else None
        result["execution"] = {
            k: outcome.get(k) for k in ("status", "code", "error", "totalInputAmount", "totalOutputAmount")
        }
        result["ok"] = outcome.get("status") == "Success"
        result["status"] = "executed" if result["ok"] else "failed"
        result["message"] = (
            f"{side} landed: {signature}"
            if result["ok"]
            else f"{side} failed with code {outcome.get('code')}: {outcome.get('error') or 'no error message'}"
        )
    except Exception as error:  # noqa: BLE001 - agents get a result, never a traceback
        result["status"] = "error"
        result["message"] = f"{side} not sent: {error}"
    return result


def buy(mint: str, amount_sol: float, config: Config | None = None) -> dict[str, Any]:
    """Buys mint with amount_sol SOL if Dexra allows it; returns a JSON-serializable dict, never raises."""
    try:
        config = config or load_config()
        decision = decide_buy(mint, amount_sol, config)
        valid = isinstance(amount_sol, (int, float)) and math.isfinite(amount_sol) and amount_sol > 0
        lamports = round(amount_sol * LAMPORTS_PER_SOL) if valid else 0
        mode = "live" if config.live else "dry_run"
        result = _result("buy", mint, SOL_MINT, mint, lamports, amount_sol, decision, mode)
        return _run(result, decision, config)
    except Exception as error:  # noqa: BLE001
        out = _result("buy", mint, SOL_MINT, mint, 0, None, None, "dry_run")
        out["message"] = f"buy not sent: {error}"
        return out


def _decimals(mint: str, config: Config) -> int | None:
    client = dexra_for(config)
    try:
        decimals = (client.describe_address(mint) or {}).get("stats", {}).get("decimals")
        return decimals if isinstance(decimals, int) else None
    except Exception:  # noqa: BLE001 - unknown decimals only skips the preflight
        return None
    finally:
        client.close()


def sell(mint: str, amount_atomic: int | str, config: Config | None = None) -> dict[str, Any]:
    """Sells amount_atomic (smallest units) of mint for SOL; reports the preflight but never blocks; never raises."""
    try:
        config = config or load_config()
        atomic = int(amount_atomic)
        if atomic <= 0:
            raise ValueError("amount must be above 0")
        decimals = _decimals(mint, config)
        tokens = None if decimals is None else atomic / 10**decimals
        decision = decide_sell(mint, tokens, config)
        mode = "live" if config.live else "dry_run"
        result = _result("sell", mint, mint, SOL_MINT, atomic, tokens, decision, mode)
        return _run(result, decision, config)
    except Exception as error:  # noqa: BLE001
        out = _result("sell", mint, mint, SOL_MINT, 0, None, None, "dry_run")
        out["message"] = f"sell not sent: {error}"
        return out
