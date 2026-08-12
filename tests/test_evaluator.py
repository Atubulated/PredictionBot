"""Settlement grader (evaluator.evaluate_bet) — market-family dispatch.

These lock in the fix for the substring-matching grader that mis-graded live
slips: a 0-0 draw shown as "1X2 Away WON", away wins shown as "1X2 Away LOST"
and "DC Home/Away LOST", handicaps always lost, and corners graded against
goals. The leg is stored as the combined "{market} - {selection}" string, so
every case below feeds that exact format.
"""
from predictionbot.evaluator import evaluate_bet, is_corners_market


# --- 1X2 / Match Winner: the headline regression -------------------------------
def test_1x2_away_on_goalless_draw_is_lost_not_won():
    # The exact bug: "1x2 - away" contains "1x", so the old grader treated it as
    # Home-or-Draw and returned WON on 0-0. It must be a LOSS.
    assert evaluate_bet("1X2 - Away", 0, 0) == "lost"


def test_1x2_away_on_away_win_is_won():
    # The mirror bug: an actual away win was graded LOST. It must be a WIN.
    assert evaluate_bet("1X2 - Away", 0, 1) == "won"


def test_1x2_home_and_draw():
    assert evaluate_bet("1X2 - Home", 2, 0) == "won"
    assert evaluate_bet("1X2 - Home", 0, 1) == "lost"
    assert evaluate_bet("1X2 - Draw", 1, 1) == "won"
    assert evaluate_bet("1X2 - Draw", 1, 0) == "lost"


# --- Double Chance (slash forms) ----------------------------------------------
def test_double_chance_home_away_on_draw_is_lost():
    # "12" loses only on a draw.
    assert evaluate_bet("Double Chance - Home/Away", 1, 1) == "lost"


def test_double_chance_home_away_on_a_win_is_won():
    assert evaluate_bet("Double Chance - Home/Away", 0, 2) == "won"
    assert evaluate_bet("Double Chance - Home/Away", 2, 0) == "won"


def test_double_chance_home_draw_and_draw_away():
    assert evaluate_bet("Double Chance - Home/Draw", 0, 0) == "won"
    assert evaluate_bet("Double Chance - Home/Draw", 0, 1) == "lost"
    assert evaluate_bet("Double Chance - Draw/Away", 0, 0) == "won"
    assert evaluate_bet("Double Chance - Draw/Away", 1, 0) == "lost"


# --- Totals (goals over/under) -------------------------------------------------
def test_totals_over_under_and_push():
    assert evaluate_bet("O/U Over 2.5 - Over 2.5", 2, 1) == "won"   # 3 > 2.5
    assert evaluate_bet("O/U Over 2.5 - Over 2.5", 1, 1) == "lost"  # 2 < 2.5
    assert evaluate_bet("O/U Under 3.5 - Under 3.5", 1, 1) == "won"
    assert evaluate_bet("O/U Under 3.5 - Under 3.5", 2, 2) == "lost"
    # Whole-number line landing exactly on the total is a push -> void.
    assert evaluate_bet("O/U Over 3 - Over 3", 2, 1) == "void"


# --- BTTS ----------------------------------------------------------------------
def test_btts_yes_no():
    assert evaluate_bet("BTTS - Yes", 1, 2) == "won"
    assert evaluate_bet("BTTS - Yes", 1, 0) == "lost"
    assert evaluate_bet("BTTS - No", 3, 0) == "won"
    assert evaluate_bet("BTTS - No", 1, 1) == "lost"


# --- Draw No Bet ---------------------------------------------------------------
def test_draw_no_bet_voids_on_draw():
    assert evaluate_bet("Draw No Bet - Home", 1, 1) == "void"
    assert evaluate_bet("Draw No Bet - Home", 2, 1) == "won"
    assert evaluate_bet("Draw No Bet - Away", 1, 2) == "won"
    assert evaluate_bet("Draw No Bet - Away", 2, 1) == "lost"


# --- Asian / signed-line Handicap ----------------------------------------------
def test_handicap_signed_line():
    # "Away -1.5" needs the away side to win by 2+.
    assert evaluate_bet("AH Away -1.5 - Away -1.5", 0, 3) == "won"
    assert evaluate_bet("AH Away -1.5 - Away -1.5", 0, 1) == "lost"
    # "Home +1" is a whole-line push when home loses by exactly 1.
    assert evaluate_bet("AH Home +1 - Home +1", 0, 1) == "void"
    assert evaluate_bet("AH Home +1 - Home +1", 0, 0) == "won"
    assert evaluate_bet("AH Home +1 - Home +1", 0, 2) == "lost"


def test_handicap_keeps_sign_on_whole_numbers():
    # "Away -2" must lay two goals, not receive them: 0-2 is a push, not a win.
    assert evaluate_bet("AH Away -2 - Away -2", 0, 2) == "void"
    assert evaluate_bet("AH Away -2 - Away -2", 0, 3) == "won"
    assert evaluate_bet("AH Away -2 - Away -2", 0, 1) == "lost"


# --- Corners (needs corner stats, not goals) -----------------------------------
def test_corners_need_stats_and_grade_against_corner_count():
    # Without corner stats the leg is not gradeable from goals.
    assert evaluate_bet("Corners O/U Over 8.5 - Over 8.5", 1, 0) == "unsettleable"
    # With a real corner count it grades against corners, not the 1-0 goals.
    assert evaluate_bet("Corners O/U Over 8.5 - Over 8.5", 1, 0, corners_total=9) == "won"
    assert evaluate_bet("Corners O/U Over 8.5 - Over 8.5", 1, 0, corners_total=8) == "lost"
    assert evaluate_bet("Corners O/U Under 9.5 - Under 9.5", 5, 4, corners_total=7) == "won"


def test_is_corners_market():
    assert is_corners_market("Corners O/U Over 8.5 - Over 8.5")
    assert not is_corners_market("O/U Over 2.5 - Over 2.5")
    assert not is_corners_market("1X2 - Home")


# --- Defensive fallbacks -------------------------------------------------------
def test_missing_scores_and_unknown_market_void_rather_than_lose():
    assert evaluate_bet("1X2 - Home", None, 1) == "void"
    assert evaluate_bet("Some Weird Market - Whatever", 1, 0) == "void"
