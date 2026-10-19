"""The explicit pipeline: scan -> check -> decide -> trade -> report.

Every path to a trade passes the Dexra guard twice: once in `check`, so the decision is visible
before anything happens, and again inside the core `buy`, so a trade can never skip it.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Checkpointer, interrupt

from dexra_core import guard, trade
from dexra_core.config import Config, load_config

from .tools import fresh_launches_result


class TradeState(TypedDict, total=False):
    mint: str | None
    amount_sol: float
    minutes: int
    scan_note: str
    decision: dict[str, Any] | None
    approved: bool
    approval_note: str
    trade: dict[str, Any] | None
    report: str


def _is_yes(answer: Any) -> bool:
    if isinstance(answer, bool):
        return answer
    if isinstance(answer, dict):
        return _is_yes(answer.get("approved"))
    return str(answer).strip().lower() in ("y", "yes", "true", "approve", "approved")


def build_graph(
    config: Config | None = None,
    *,
    require_approval: bool = True,
    checkpointer: Checkpointer | None = None,
):
    """Compiles the trading graph. A checkpointer is required for the approval interrupt."""
    config = config or load_config()
    passing = ("ok", "caution") if config.allow_caution else ("ok",)

    def scan(state: TradeState) -> TradeState:
        if state.get("mint"):
            return {"scan_note": f"checking the given mint {state['mint']}"}
        minutes = state.get("minutes", 30)
        result = fresh_launches_result(minutes=minutes, limit=20, config=config)
        if "error" in result:
            return {"mint": None, "scan_note": f"new_launches failed: {result['error']}"}
        launches = result["launches"]
        pick = next((x for x in launches if x.get("verdict") in passing), None)
        if pick is None:
            return {
                "mint": None,
                "scan_note": f"{len(launches)} launches in the last {minutes} minutes, none with a passing verdict",
            }
        name = pick.get("symbol") or pick.get("name") or "unnamed"
        return {
            "mint": pick["mint"],
            "scan_note": f"picked {name} ({pick['mint']}) from {len(launches)} launches, launch verdict {pick.get('verdict')}",
        }

    def check(state: TradeState) -> TradeState:
        decision = guard.decide_buy(state.get("mint") or "", state.get("amount_sol", 0.01), config)
        return {"decision": decision.to_dict()}

    def decide(state: TradeState) -> TradeState:
        decision = state.get("decision") or {}
        if not decision.get("allowed"):
            return {"approved": False, "approval_note": "blocked by the guard"}
        if not config.live:
            return {"approved": True, "approval_note": f"auto: dry run ({config.dry_run_reason})"}
        if not require_approval:
            return {"approved": True, "approval_note": "auto: REQUIRE_APPROVAL=false"}
        # Pauses the run until a human answers; resume with Command(resume=True or False).
        answer = interrupt(
            {
                "question": f"Send a LIVE buy of {decision['amount']} SOL of {decision['mint']}?",
                "mint": decision["mint"],
                "amount_sol": decision["amount"],
                "verdicts": decision["verdicts"],
                "reasons": decision["reasons"],
            }
        )
        approved = _is_yes(answer)
        return {"approved": approved, "approval_note": "approved by a human" if approved else "declined by a human"}

    def trade_node(state: TradeState) -> TradeState:
        # The core runs the guard again: verdicts can change while a human thinks it over.
        return {"trade": trade.buy(state.get("mint") or "", state.get("amount_sol", 0.01), config)}

    def report(state: TradeState) -> TradeState:
        lines = [f"Scan: {state.get('scan_note', '-')}"]
        decision = state.get("decision")
        if decision:
            v = decision["verdicts"]
            verdict = "allowed" if decision["allowed"] else "BLOCKED"
            lines.append(f"Guard: {verdict} (token_deep_check {v['token']}, swap_preflight {v['preflight']})")
            lines += [f"  - {r}" for r in decision["reasons"]]
        if state.get("approval_note"):
            lines.append(f"Approval: {state['approval_note']}")
        result = state.get("trade")
        if result:
            lines.append(f"Trade: {result['status']} ({result['mode']}): {result['message']}")
            if result.get("explorerUrl"):
                lines.append(f"  {result['explorerUrl']}")
        if not config.live:
            lines.append(f"Dry run: nothing was sent ({config.dry_run_reason}).")
        return {"report": "\n".join(lines)}

    def after_scan(state: TradeState) -> Literal["check", "report"]:
        return "check" if state.get("mint") else "report"

    def after_decide(state: TradeState) -> Literal["trade", "report"]:
        return "trade" if state.get("approved") else "report"

    builder = StateGraph(TradeState)
    builder.add_node("scan", scan)
    builder.add_node("check", check)
    builder.add_node("decide", decide)
    builder.add_node("trade", trade_node)
    builder.add_node("report", report)
    builder.add_edge(START, "scan")
    builder.add_conditional_edges("scan", after_scan)
    builder.add_edge("check", "decide")
    builder.add_conditional_edges("decide", after_decide)
    builder.add_edge("trade", "report")
    builder.add_edge("report", END)
    return builder.compile(checkpointer=checkpointer or InMemorySaver())
