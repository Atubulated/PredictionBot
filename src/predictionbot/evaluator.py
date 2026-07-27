# src/predictionbot/evaluator.py
import re

def evaluate_bet(selection: str, home_score: int, away_score: int) -> bool:
    """
    Evaluates if a bet won based on the final score.
    Returns True for WIN, False for LOSS.
    """
    if home_score is None or away_score is None:
        return False # Match not finished or abandoned

    sel = selection.lower()

    # 1. Double Chance
    if "home or draw" in sel or "1x" in sel:
        return home_score >= away_score
    if "home or away" in sel or "12" in sel:
        return home_score != away_score
    if "draw or away" in sel or "x2" in sel:
        return away_score >= home_score

    # 2. Match Result (1X2)
    if sel in ["home", "1", "home win"]:
        return home_score > away_score
    if sel in ["away", "2", "away win"]:
        return away_score > home_score
    if sel in ["draw", "x"]:
        return home_score == away_score

    # 3. Totals (Over/Under)
    if "over" in sel:
        match = re.search(r'\d+\.?\d*', sel)
        if match:
            line = float(match.group())
            return (home_score + away_score) > line
    if "under" in sel:
        match = re.search(r'\d+\.?\d*', sel)
        if match:
            line = float(match.group())
            return (home_score + away_score) < line

    # 4. Both Teams to Score
    if "yes" in sel and "btts" in sel or "both teams" in sel and "yes" in sel:
        return home_score > 0 and away_score > 0
    if "no" in sel and "btts" in sel or "both teams" in sel and "no" in sel:
        return home_score == 0 or away_score == 0

    return False # Fallback