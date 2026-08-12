"""Pure settlement logic for consolidated bet-slip result sheets.

Kept free of I/O (no Supabase, no Telegram) so the notify decision and the
rendered result sheet are unit-testable. `telegram_bot.check_finished_matches`
persists each leg's terminal status, reloads the full slip, then calls
`evaluate_slip_settlement` to decide whether to send exactly one result sheet.
"""

from __future__ import annotations

TERMINAL_STATUSES = ("won", "lost", "void")


def settlement_score(leg: dict) -> str:
    """Best-effort score for a settled leg; falls back when no score was persisted."""
    return leg.get("final_score") or "settled"


def evaluate_slip_settlement(legs: list[dict]) -> dict:
    """Decide whether a slip is ready to notify and render its result sheet.

    A slip is notified once, and only when EVERY leg is terminal
    (``won``/``lost``/``void``). Pending, missing, or not-yet-started legs
    leave the slip unnotified for the next polling cycle.

    Returns a dict:
      notify:  True only when every leg is terminal AND not already notified.
      reason:  "incomplete" | "already_notified" | "ready"
      result:  "won" | "lost" | "void" | None
      message: the consolidated result sheet (str) when notify is True, else "".
    """
    if not legs:
        return {"notify": False, "reason": "incomplete", "result": None, "message": ""}

    slip_id = legs[0].get("slip_id", "")

    # Idempotency: if any leg already carries a notification marker, never resend.
    if any(leg.get("settlement_notified_at") for leg in legs):
        return {"notify": False, "reason": "already_notified", "result": None, "message": ""}

    buckets: dict[str, list[dict]] = {"won": [], "lost": [], "void": []}
    unsettled = 0
    for leg in legs:
        status = leg.get("status", "pending")
        if status in buckets:
            buckets[status].append(leg)
        else:
            unsettled += 1

    # Wait until EVERY leg is terminal before sending anything.
    if unsettled > 0:
        return {"notify": False, "reason": "incomplete", "result": None, "message": ""}

    # Final result derived from all persisted legs, not a per-poll accumulator.
    if buckets["lost"]:
        result = "lost"
        headline = f" **Slip Lost** ({slip_id})"
    elif buckets["won"]:
        result = "won"
        headline = f"🎉 **SLIP WON!** ({slip_id})"
    else:  # only voids
        result = "void"
        headline = f"♻️ **Slip Void** ({slip_id})"

    msg = f"{headline}\n\n"
    total_odds = 1.0
    for leg in sorted(legs, key=lambda x: x.get("id", 0)):
        status = leg["status"]
        label = leg.get("fixture_label", "")
        selection = leg.get("selection", "")
        if status == "won":
            msg += f"✅ {label} ({settlement_score(leg)})\n   ➔ {selection}\n\n"
        elif status == "lost":
            msg += f"❌ {label} ({settlement_score(leg)})\n   ➔ {selection}\n\n"
        else:  # void
            msg += f"♻️ {label} — void\n   ➔ {selection}\n\n"
        # Void legs multiply as 1.0 (stake returned for that leg).
        if status != "void":
            try:
                total_odds *= float(leg.get("odds") or 1.0)
            except (TypeError, ValueError):
                pass

    if result == "won":
        msg += f"💵 Payout: {total_odds:.2f}x"
    else:
        msg += f"💵 Slip odds were {total_odds:.2f}x"

    return {"notify": True, "reason": "ready", "result": result, "message": msg}
