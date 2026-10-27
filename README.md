# Dexra LangGraph agent

A [LangGraph](https://docs.langchain.com/oss/python/langgraph/overview) agent that trades on Solana and asks [Dexra](https://dexra.dev/?utm_source=github&utm_medium=referral&utm_campaign=templates&utm_content=template-langgraph) before every buy. Each run follows one visible path: scan for a token, check it with `token_deep_check` and `swap_preflight`, decide (a blocked token goes straight to the report), trade through Jupiter, and report the verdict and reasons in plain words. A second mode gives an LLM the same tools for free-form chat. Everything is a dry run until you switch live trading on.

```
scan ──► check ──► decide ──► trade ──► report
  │                  │                    ▲
  └── no token ──────┴── blocked/declined ┘
```

## Safety

- **Dry run by default.** Trades are sent only when `DRY_RUN=false`, `SOLANA_PRIVATE_KEY` and `JUPITER_API_KEY` are all set. Otherwise the result says which one is missing.
- **Caps.** `MAX_TRADE_SOL` (default 0.05) is a hard cap per buy; a larger buy is refused, not reduced. `SLIPPAGE_BPS` defaults to 100 and cannot go above 1000.
- **What blocks a buy:** a verdict of `avoid` or `unknown` from either check, `caution` unless `ALLOW_CAUTION=true`, an invalid mint or amount, and any Dexra failure (402, 429, 5xx, network). It fails closed.
- **The guard runs twice.** Once in the `check` node so you see the decision, and again inside the core `buy` right before the swap. No path in the graph or the chat agent reaches Jupiter without it.
- **Human approval when live.** With `REQUIRE_APPROVAL=true` (the default), a live buy pauses the graph with a LangGraph interrupt and waits for a yes. Chat mode asks before every live `buy` and `sell`. Dry runs never ask.
- **Sells are never blocked**, so a position can always be exited. The preflight verdict is still reported.
- Keys come only from the environment. The wallet secret is never logged or echoed. Use a dedicated wallet that holds little SOL: the caps limit each trade, not all trades together.

Not financial advice. A Dexra `ok` means launches like it survived more often in our published report, not that the price will go up.

## Quick start

Click **Use this template** on GitHub, or:

```bash
git clone https://github.com/dexra-dev/template-langgraph
cd template-langgraph
cp .env.example .env        # add DEXRA_API_KEY at least
uv sync
```

Graph mode needs no LLM:

```bash
uv run --env-file .env dexra-graph --mint DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263 --amount 0.01
uv run --env-file .env dexra-graph --minutes 30     # scan fresh launches, check the first passing one
uv run --env-file .env dexra-graph --mint <MINT> --json
```

Chat mode needs `ANTHROPIC_API_KEY` (or `LLM_PROVIDER=openai` and `OPENAI_API_KEY`):

```bash
uv run --env-file .env dexra-agent "Check BONK and buy 0.01 SOL if it passes"
uv run --env-file .env dexra-agent       # interactive; type exit to quit
```

Get a key at [dexra.dev/app/keys](https://dexra.dev/app/keys?utm_source=github&utm_medium=referral&utm_campaign=templates&utm_content=template-langgraph). The free tier gives 100 credits a day. Paid tools can also be paid per call over x402.

## Configuration

| Variable | Default | Meaning |
| - | - | - |
| `DEXRA_API_KEY` | unset | `dxr_…` key. Without it, paid checks answer 402 and every buy is blocked. |
| `DEXRA_BASE_URL` | `https://api.dexra.dev` | Dexra API. Point it at a local API for testing. |
| `DRY_RUN` | `true` | Only `false`/`0`/`no`/`off` turn it off. |
| `JUPITER_API_KEY` | unset | Needed for live trading. In a dry run it adds a Jupiter quote. |
| `SOLANA_PRIVATE_KEY` | unset | Base58 64-byte secret key. Only for live trading. |
| `MAX_TRADE_SOL` | `0.05` | Hard cap per buy. |
| `SLIPPAGE_BPS` | `100` | Jupiter slippage, 0 to 1000. |
| `ALLOW_CAUTION` | `false` | Let a `caution` verdict pass. |
| `BUY_AMOUNT_SOL` | `0.01` | SOL per buy in graph mode when `--amount` is not given. |
| `REQUIRE_APPROVAL` | `true` | Ask a human before live trades. |
| `LLM_PROVIDER` | `anthropic` | `anthropic` or `openai`. Chat mode only. |
| `LLM_MODEL` | `claude-sonnet-5-5` / `gpt-5.6-luna` | Model for the provider. |
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` | unset | Key for the chosen provider. |
| `USE_DEXRA_MCP` | `false` | Add Dexra's remote MCP tools to chat mode. |
| `DEXRA_MCP_URL` | `$DEXRA_BASE_URL/mcp` | Dexra MCP endpoint (Streamable HTTP, `Authorization: Bearer $DEXRA_API_KEY`). |

## Already have a bot?

You don't need LangGraph or this template to buy only what Dexra marks `ok`. [examples/whitelist_gate.py](examples/whitelist_gate.py) is one file that uses only the Python standard library. Copy it into your bot and call it right before your swap:

```python
from whitelist_gate import dexra_allows_buy

gate = dexra_allows_buy(mint, amount_sol)
if not gate.allowed:
    log.info("skip %s: %s", mint, gate.reasons)
    return
your_swap(mint, amount_sol)
```

- It runs `token_deep_check` and `swap_preflight` in parallel. The buy passes only if both say `ok`; `allow_caution=True` also accepts `caution`.
- Without `DEXRA_API_KEY` it is off and allows every buy, so adding it changes nothing until you set a key.
- On a Dexra error (402, 429, 5xx, network) it blocks the buy. Pass `fail_open=True` to keep buying as before and only log the reason.
- Each check is two Dexra calls. Run your own filters first so only real candidates reach it. In an asyncio bot, call it with `await asyncio.to_thread(dexra_allows_buy, mint, amount_sol)`.

Try it on one mint: `DEXRA_API_KEY=dxr_… python examples/whitelist_gate.py <mint> 0.01`.

## How the guard works

`dexra_core` is the shared Dexra trading core, copied unchanged. `decide_buy` checks the mint and amount first, then runs `token_deep_check` and `swap_preflight` in parallel and allows the buy only if both verdicts pass. `buy` runs that decision, then quotes and executes through Jupiter Swap API v2, or stops at a dry run. The full rules are in [`dexra_core/README.md`](dexra_core/README.md).

The graph (`dexra_agent/graph.py`):

- `scan` uses the given `--mint`, or calls `new_launches` and picks the first launch whose own verdict passes.
- `check` calls `decide_buy` and stores the decision in the graph state.
- `decide` routes a blocked decision to `report`. An allowed one goes to `trade` straight away in a dry run, or after a human answers the interrupt when live.
- `trade` calls the core `buy`, which checks again.
- `report` writes the verdicts, reasons, approval and trade result as text.

The graph is compiled with an `InMemorySaver` checkpointer, which interrupts need. Swap in a durable checkpointer if you want a paused approval to survive a restart.

Chat mode (`dexra_agent/agent.py`) uses `langchain.agents.create_agent` with tools `check_token`, `preflight`, `fresh_launches`, `buy` and `sell`. With `USE_DEXRA_MCP=true` it also loads Dexra's MCP tools through `langchain.mcp`, keeping only read-only tools whose names don't clash with the local ones, so trades always go through the core guard.

## Project layout

```
dexra_core/            shared trading core (guard, Dexra client, Jupiter, buy/sell), unchanged
dexra_agent/
  graph.py             scan -> check -> decide -> trade -> report
  agent.py             ReAct chat agent, model choice, Dexra MCP tools
  tools.py             LangChain tools on top of dexra_core
  prompts.py           system prompt
  settings.py          agent settings from the environment
  cli.py               dexra-graph and dexra-agent
examples/whitelist_gate.py   standalone pre-buy gate for an existing bot
tests/                 core tests plus graph, tools and agent tests (mocked HTTP, fake model)
```

Checks: `uv run ruff check .`, `uv run mypy`, `uv run pytest`. Tests make no network or LLM calls.

## Links

- [Dexra template docs](https://dexra.dev/docs/templates?utm_source=github&utm_medium=referral&utm_campaign=templates&utm_content=template-langgraph)
- [API keys](https://dexra.dev/app/keys?utm_source=github&utm_medium=referral&utm_campaign=templates&utm_content=template-langgraph)
- [Published accuracy report](https://dexra.dev/accuracy?utm_source=github&utm_medium=referral&utm_campaign=templates&utm_content=template-langgraph)
- [LangGraph docs](https://docs.langchain.com/oss/python/langgraph/overview), [interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts), [LangChain agents](https://docs.langchain.com/oss/python/langchain/agents), [MCP](https://docs.langchain.com/oss/python/langchain/mcp)
- [Jupiter Swap API](https://developers.jup.ag/docs/swap/order-and-execute)

MIT licensed.
