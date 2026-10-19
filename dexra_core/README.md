# Dexra trading core

The shared core that every Dexra agent template copies. It checks a token with [Dexra](https://dexra.dev) before a buy, then quotes and executes the swap through [Jupiter Swap API v2](https://developers.jup.ag/docs/swap/order-and-execute) (`GET /order`, `POST /execute`). It runs as a dry run unless you switch live trading on explicitly.

There are two implementations with the same behaviour:

| | Copy this | Dependencies | Check it |
| - | - | - | - |
| TypeScript (Node 20.18+, ESM) | `ts/src/` | `@dexra-dev/client`, `@solana/kit` | `npm test`, `npm run typecheck`, `npm run smoke` |
| Python (3.11+) | `py/dexra_core/` | `httpx`, `solders` | `uv run pytest`, `uv run python scripts/smoke.py` |

## Settings (environment)

| Variable | Default | Meaning |
| - | - | - |
| `DEXRA_API_KEY` | unset | `dxr_…` key. Without it, paid checks answer 402, so every buy is blocked. |
| `DEXRA_BASE_URL` | `https://api.dexra.dev` | Point it at a local API for testing. |
| `JUPITER_API_KEY` | unset | Sent as `x-api-key`. Needed for live trading. In a dry run it also adds a Jupiter quote to the result; without it, quoting is skipped. |
| `SOLANA_PRIVATE_KEY` | unset | Base58 64-byte secret key. It is never logged, serialized or echoed in errors. |
| `DRY_RUN` | `true` | Only `false`/`0`/`no`/`off` turn it off. |
| `MAX_TRADE_SOL` | `0.05` | Hard cap per buy. A larger buy is refused, not reduced. |
| `SLIPPAGE_BPS` | `100` | Sent to Jupiter as `slippageBps`. Must be a whole number from 0 to 1000. |
| `ALLOW_CAUTION` | `false` | When true, a `caution` verdict passes. |

Trades are sent only when `DRY_RUN=false`, `SOLANA_PRIVATE_KEY` is set and `JUPITER_API_KEY` is set. If any one is missing, the core stays in dry run and `dryRunReason` / `dry_run_reason` says which. An invalid value raises `ConfigError` from `loadConfig()` / `load_config()`. `buy` and `sell` catch that error and return it as an error result.

## Safety rules

Buys (`decideBuy`, `decide_buy`):

1. The mint must look like a Solana address, and the amount must be above 0 and no more than `MAX_TRADE_SOL`. This is checked before any network call.
2. `token_deep_check` and `swap_preflight` (`side=buy`, amount in SOL) run in parallel.
3. Each verdict must be `ok`, or `caution` when `ALLOW_CAUTION=true`. `avoid` and `unknown` always block, and so does a missing verdict.
4. Any Dexra failure blocks the buy (fail closed). That includes 402, 429, 5xx and network errors.

Sells (`decideSell`, `decide_sell`) are never blocked by a verdict, so a position can always be exited. `swap_preflight` (`side=sell`, in token units) still runs, and its verdict and reasons go into the result for logging. If Dexra fails, or the mint's decimals can't be read, the sell goes ahead with a note. Only an invalid mint or a zero amount is refused.

## API

TypeScript names are shown; Python uses snake_case for the same functions. Every function takes an optional `config` as its last argument (default `loadConfig()`).

- `loadConfig(env?) → Config`
- Dexra (`dexra.ts` / `dexra_client.py`)
  - `checkToken(mint)`
  - `preflight(mint, side, amount)`
  - `freshLaunches({minutes, limit, min_liquidity_usd, platforms})`
  - `describeAddress(address)`
  - Each returns the tool's `result`: `verdict`, `reasons`, `summary` and tool-specific fields.
  - Python: `DexraClient(api_key, base_url)` with `token_deep_check`, `swap_preflight`, `new_launches`, `describe_address` and `request`. Failures raise `DexraError(status, kind, message)`.
- `decideBuy(mint, amountSol) → Decision`
- `decideSell(mint, amountTokens | null) → Decision`
  - `Decision` is `{side, mint, amount, allowed, verdicts: {token, preflight}, reasons[], summaries: {token, preflight}}`.
- Jupiter (`jupiter.ts` / `jupiter.py`)
  - `order({inputMint, outputMint, amount, taker?, slippageBps?})` calls `GET https://api.jup.ag/swap/v2/order`. `amount` is in atomic units. Without `taker`, the response is a quote with `transaction: null`.
  - `execute({signedTransaction, requestId})` calls `POST /execute`.
  - Failures raise `JupiterError`.
- `buy(mint, amountSol) → TradeResult`
- `sell(mint, amountAtomic) → TradeResult`
  - Neither one throws.
  - `buy` spends SOL. `sell` sells the token for SOL; it reads the mint's decimals with the free `describe_address` so the preflight gets token units.

`TradeResult` is plain JSON, so you can hand it straight to an LLM:

```json
{
  "ok": true,
  "status": "dry_run",
  "mode": "dry_run",
  "side": "buy",
  "mint": "DezX…",
  "input": {"mint": "So111…", "amountAtomic": "10000000", "amount": 0.01},
  "outputMint": "DezX…",
  "decision": {"allowed": true, "verdicts": {"token": "ok", "preflight": "ok"}, "reasons": ["…"]},
  "quote": {"requestId": "…", "inAmount": "…", "outAmount": "…", "minOutAmount": "…", "priceImpactPct": -0.09, "slippageBps": 100, "router": "okx", "feeBps": 10},
  "signature": null,
  "explorerUrl": null,
  "execution": null,
  "message": "would buy 10000000 atomic units of So111… (dry run: DRY_RUN is not false)"
}
```

`status` is one of:

- `blocked`: the guard said no.
- `dry_run`: it would have traded.
- `executed`: `/execute` returned `Success`.
- `failed`: Jupiter could not build the transaction, or `/execute` returned `Failed`. Its `code` is in `execution`.
- `error`: config, key or network trouble.

## Live path

1. Run the guard.
2. Call `/order` with `taker` set to the wallet address and `slippageBps`.
3. If `transaction` is `""` or null, stop with `failed`.
4. Decode the base64 transaction and sign the wallet's slot.
   - The signing is partial on purpose, because JupiterZ (RFQ) routes carry a market maker signature that `/execute` adds later.
   - TS uses `@solana/kit` `partiallySignTransaction`. Python uses `solders`, setting the signature in the wallet's slot.
5. Call `/execute` with `{signedTransaction, requestId}`.
6. Return the signature and a Solscan link.

The core does not ask for v1 transactions (`maxSupportedTransactionVersion` is left at its default of `"0"`), so `/order` always returns v0.

Setting `slippageBps` puts Jupiter's order into `mode: "manual"`. Jupiter's docs say optional parameters can change how an order is routed, but their routing matrix only names `payer` and `excludeRouters` as removing routers. To let Jupiter choose slippage itself (RTSE), drop the parameter in `order()`.

Use a dedicated wallet that holds little SOL. The caps limit how much each trade can lose, not how much all trades together can lose.
