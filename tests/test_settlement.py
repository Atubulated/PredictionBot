from predictionbot.settlement import evaluate_slip_settlement


def leg(identifier, status, *, odds=1.5, score=None, notified=None):
    row = {
        "id": identifier,
        "slip_id": "slip-1",
        "fixture_label": f"Team {identifier} vs Opponent",
        "selection": "Over 1.5",
        "odds": odds,
        "status": status,
    }
    if score is not None:
        row["final_score"] = score
    if notified is not None:
        row["settlement_notified_at"] = notified
    return row


def test_partial_slip_stays_pending():
    decision = evaluate_slip_settlement([
        leg(1, "won", score="2-0"),
        leg(2, "pending"),
    ])

    assert decision == {
        "notify": False,
        "reason": "incomplete",
        "result": None,
        "message": "",
    }


def test_mixed_won_lost_slip_notifies_one_complete_loss_sheet():
    decision = evaluate_slip_settlement([
        leg(1, "won", score="2-0"),
        leg(2, "lost", score="0-1"),
    ])

    assert decision["notify"] is True
    assert decision["result"] == "lost"
    assert "SLIP WON" not in decision["message"]
    assert "Slip Lost" in decision["message"]
    assert "Team 1 vs Opponent (2-0)" in decision["message"]
    assert "Team 2 vs Opponent (0-1)" in decision["message"]


def test_all_won_slip_notifies_with_all_legs():
    decision = evaluate_slip_settlement([
        leg(1, "won", odds=1.4, score="2-0"),
        leg(2, "won", odds=1.6, score="1-0"),
    ])

    assert decision["notify"] is True
    assert decision["result"] == "won"
    assert "SLIP WON" in decision["message"]
    assert "Payout: 2.24x" in decision["message"]


def test_void_leg_is_terminal_and_included_in_result_sheet():
    decision = evaluate_slip_settlement([
        leg(1, "won", score="1-0"),
        leg(2, "void"),
    ])

    assert decision["notify"] is True
    assert decision["result"] == "won"
    assert "Team 2 vs Opponent — void" in decision["message"]
    assert "Payout: 1.50x" in decision["message"]


def test_repeated_poll_does_not_notify_again():
    decision = evaluate_slip_settlement([
        leg(1, "won", score="2-0", notified="2026-08-07T12:00:00+00:00"),
        leg(2, "won", score="1-0", notified="2026-08-07T12:00:00+00:00"),
    ])

    assert decision == {
        "notify": False,
        "reason": "already_notified",
        "result": None,
        "message": "",
    }
