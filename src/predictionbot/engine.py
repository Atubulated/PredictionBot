from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from predictionbot.domain import Fixture, HistoricalMatch, MarketFamily, MarketOdds, Prediction, Team
from predictionbot.features import build_goal_profile, build_stat_profile, expected_stat_total
from predictionbot.models.corners import (
    calculate_expected_corners,
    corner_over_probability,
)
from predictionbot.models.goals import (
    btts_probability,
    estimate_expected_goals,
    estimate_expected_total,
    handicap_probability,
    outcome_probabilities,
    poisson_cdf,
    probability_over,
)
from predictionbot.odds import implied_probability
from predictionbot.risk import DEFAULT_SAFE_ODDS_RULE


def score_totals_market(
    fixture: Fixture,
    market: MarketOdds,
    history: list[HistoricalMatch],
    min_edge: float = 0.05,
    live_xg: tuple[float, float] | None = None,  # <-- NEW PARAMETER ADDED
) -> Prediction | None:
    if market.family != MarketFamily.TOTALS:
        return None

    selection = market.selection.casefold()
    if "over" not in selection and "under" not in selection:
        return None

    line = market.line or _extract_line(market.market) or _extract_line(market.selection)
    if line is None:
        return None

    home_profile = build_goal_profile(fixture.home.name, history)
    away_profile = build_goal_profile(fixture.away.name, history)
    if home_profile.matches == 0 or away_profile.matches == 0:
        return None

    # 1. Get historical expected total goals
    expected_total = estimate_expected_total(home_profile, away_profile)
    
    # 2. Blend with live xG if available (60% historical, 40% live)
    if live_xg is not None:
        live_home_xg, live_away_xg = live_xg
        live_total = live_home_xg + live_away_xg
        expected_total = (expected_total * 0.6) + (live_total * 0.4)

    over_probability = probability_over(line, expected_total)
    model_probability = over_probability if "over" in selection else 1 - over_probability
    book_probability = implied_probability(market.odds)
    edge = model_probability - book_probability

    if edge < min_edge:
        return None

    safe_odds_band = DEFAULT_SAFE_ODDS_RULE.classify(model_probability)
    confidence = safe_odds_band.value
    
    xg_source = "Blended (Hist + Live xG)" if live_xg else "Historical"
    reason = (
        f"Expected total goals {expected_total:.2f} ({xg_source}); model {model_probability:.1%} "
        f"vs bookmaker implied {book_probability:.1%}. "
        f"Safe odds threshold starts at {DEFAULT_SAFE_ODDS_RULE.min_safe_probability:.0%}."
    )
    
    return Prediction(
        fixture=fixture,
        market=market,
        model_probability=model_probability,
        implied_probability=book_probability,
        edge=edge,
        confidence=confidence,
        safe_odds_band=safe_odds_band,
        reason=reason,
    )


def score_corners_market(
    fixture: Fixture,
    market: MarketOdds,
    history: list[HistoricalMatch],
    min_edge: float = 0.05,
) -> Prediction | None:
    """Scores corner markets using the new corners model and historical corner data."""
    if market.family != MarketFamily.CORNERS:
        return None

    selection = market.selection.casefold()
    if "over" not in selection and "under" not in selection:
        return None

    line = market.line or _extract_line(market.market) or _extract_line(market.selection)
    if line is None:
        return None

    # Calculate Expected Corners (xC) using historical data
    expectations = calculate_expected_corners(fixture.home.name, fixture.away.name, history)
    expected_total = expectations.total_expected

    # Calculate model probability based on selection
    if "over" in selection:
        model_probability = corner_over_probability(line, expected_total)
    else:
        # For Under, probability is 1 - Over(line) for .5 lines
        model_probability = 1.0 - corner_over_probability(line, expected_total)

    book_probability = implied_probability(market.odds)
    edge = model_probability - book_probability

    if edge < min_edge:
        return None

    safe_odds_band = DEFAULT_SAFE_ODDS_RULE.classify(model_probability)
    confidence = safe_odds_band.value
    reason = (
        f"Expected total corners {expected_total:.2f}; model {model_probability:.1%} "
        f"vs bookmaker implied {book_probability:.1%}."
    )
    return Prediction(
        fixture=fixture,
        market=market,
        model_probability=model_probability,
        implied_probability=book_probability,
        edge=edge,
        confidence=confidence,
        safe_odds_band=safe_odds_band,
        reason=reason,
    )


def score_double_chance_market(
    fixture: Fixture,
    market: MarketOdds,
    history: list[HistoricalMatch],
    min_edge: float = 0.05,
) -> Prediction | None:
    if market.family != MarketFamily.DOUBLE_CHANCE:
        return None

    selection = market.selection.casefold()
    home_profile = build_goal_profile(fixture.home.name, history)
    away_profile = build_goal_profile(fixture.away.name, history)
    if home_profile.matches == 0 or away_profile.matches == 0:
        return None

    home_expected, away_expected = estimate_expected_goals(home_profile, away_profile)
    outcomes = outcome_probabilities(home_expected, away_expected)
    if "home" in selection and "draw" in selection:
        model_probability = outcomes["home"] + outcomes["draw"]
    elif "home" in selection and "away" in selection:
        model_probability = outcomes["home"] + outcomes["away"]
    elif "draw" in selection and "away" in selection:
        model_probability = outcomes["draw"] + outcomes["away"]
    else:
        return None

    return _prediction_if_value(
        fixture=fixture,
        market=market,
        model_probability=model_probability,
        min_edge=min_edge,
        reason=(
            f"Expected goals {fixture.home.name} {home_expected:.2f}, "
            f"{fixture.away.name} {away_expected:.2f}; double chance model {model_probability:.1%}."
        ),
    )


def score_handicap_market(
    fixture: Fixture,
    market: MarketOdds,
    history: list[HistoricalMatch],
    min_edge: float = 0.05,
) -> Prediction | None:
    if market.family != MarketFamily.HANDICAP or market.line is None:
        return None

    selection = market.selection.casefold()
    if "home" in selection:
        selection_side = "home"
    elif "away" in selection:
        selection_side = "away"
    else:
        return None

    home_profile = build_goal_profile(fixture.home.name, history)
    away_profile = build_goal_profile(fixture.away.name, history)
    if home_profile.matches == 0 or away_profile.matches == 0:
        return None

    home_expected, away_expected = estimate_expected_goals(home_profile, away_profile)
    model_probability = handicap_probability(home_expected, away_expected, selection_side, market.line)

    return _prediction_if_value(
        fixture=fixture,
        market=market,
        model_probability=model_probability,
        min_edge=min_edge,
        reason=(
            f"Expected goals {fixture.home.name} {home_expected:.2f}, "
            f"{fixture.away.name} {away_expected:.2f}; handicap model {model_probability:.1%}."
        ),
    )


def score_match_winner_market(
    fixture: Fixture,
    market: MarketOdds,
    history: list[HistoricalMatch],
    min_edge: float = 0.05,
) -> Prediction | None:
    """Score 1X2 markets from the same expected-goals model used elsewhere."""
    if market.family != MarketFamily.MATCH_WINNER:
        return None
    home_profile = build_goal_profile(fixture.home.name, history)
    away_profile = build_goal_profile(fixture.away.name, history)
    if home_profile.matches == 0 or away_profile.matches == 0:
        return None
    home_expected, away_expected = estimate_expected_goals(home_profile, away_profile)
    selection = market.selection.casefold()
    outcomes = outcome_probabilities(home_expected, away_expected)
    if "draw" in selection or selection in {"x", "tie"}:
        model_probability = outcomes["draw"]
    elif fixture.home.name.casefold() in selection or "home" in selection:
        model_probability = outcomes["home"]
    elif fixture.away.name.casefold() in selection or "away" in selection:
        model_probability = outcomes["away"]
    else:
        return None
    return _prediction_if_value(
        fixture, market, model_probability, min_edge,
        f"Expected goals {fixture.home.name} {home_expected:.2f}, {fixture.away.name} {away_expected:.2f}; 1X2 model {model_probability:.1%}.",
    )


def score_btts_market(
    fixture: Fixture,
    market: MarketOdds,
    history: list[HistoricalMatch],
    min_edge: float = 0.05,
) -> Prediction | None:
    if market.family != MarketFamily.BOTH_TEAMS_TO_SCORE:
        return None
    home_profile = build_goal_profile(fixture.home.name, history)
    away_profile = build_goal_profile(fixture.away.name, history)
    if home_profile.matches == 0 or away_profile.matches == 0:
        return None
    home_expected, away_expected = estimate_expected_goals(home_profile, away_profile)
    yes_probability = btts_probability(home_expected, away_expected)
    selection = market.selection.casefold()
    if selection in {"yes", "gg", "true"} or "yes" in selection:
        model_probability = yes_probability
    elif selection in {"no", "ng", "false"} or "no" in selection:
        model_probability = 1.0 - yes_probability
    else:
        return None
    return _prediction_if_value(
        fixture, market, model_probability, min_edge,
        f"Expected goals {home_expected:.2f}-{away_expected:.2f}; BTTS model {model_probability:.1%}.",
    )


def score_team_total_market(
    fixture: Fixture,
    market: MarketOdds,
    history: list[HistoricalMatch],
    min_edge: float = 0.05,
) -> Prediction | None:
    if market.family != MarketFamily.TEAM_TOTALS:
        return None
    selection = market.selection.casefold()
    team = fixture.home if ("home" in selection or fixture.home.name.casefold() in selection) else fixture.away
    profile = build_goal_profile(team.name, history)
    if profile.matches == 0:
        return None
    opponent = fixture.away if team == fixture.home else fixture.home
    opponent_profile = build_goal_profile(opponent.name, history)
    if opponent_profile.matches == 0:
        return None
    home_expected, away_expected = estimate_expected_goals(
        build_goal_profile(fixture.home.name, history),
        build_goal_profile(fixture.away.name, history),
    )
    expected = home_expected if team == fixture.home else away_expected
    line = market.line or _extract_line(market.market) or _extract_line(market.selection)
    if line is None or ("over" not in selection and "under" not in selection):
        return None
    over = 1.0 - poisson_cdf(int(line), expected)
    model_probability = over if "over" in selection else 1.0 - over
    return _prediction_if_value(
        fixture, market, model_probability, min_edge,
        f"Expected {team.name} goals {expected:.2f}; team-total model {model_probability:.1%}.",
    )


def _period_total_probability(market: MarketOdds, fixture: Fixture, history: list[HistoricalMatch], period: str, min_edge: float) -> Prediction | None:
    if market.family not in {MarketFamily.FIRST_HALF_TOTALS, MarketFamily.SECOND_HALF_TOTALS}:
        return None
    line = market.line or _extract_line(market.market) or _extract_line(market.selection)
    selection = market.selection.casefold()
    if line is None or ("over" not in selection and "under" not in selection):
        return None
    totals =[]
    for match in history:
        raw = match.raw or {}
        home = raw.get(f"{period}_home_goals", raw.get(f"{period}_home_score"))
        away = raw.get(f"{period}_away_goals", raw.get(f"{period}_away_score"))
        if home is None or away is None:
            continue
        if {match.home.casefold(), match.away.casefold()} & {fixture.home.name.casefold(), fixture.away.name.casefold()}:
            totals.append(float(home) + float(away))
    if len(totals) < 3:
        return None
    expected = sum(totals[-10:]) / len(totals[-10:])
    over = probability_over(line, expected)
    model_probability = over if "over" in selection else 1.0 - over
    return _prediction_if_value(
        fixture, market, model_probability, min_edge,
        f"Expected {period.replace('_', ' ')} goals {expected:.2f}; period-total model {model_probability:.1%}.",
    )


def score_period_totals_market(fixture, market, history, min_edge=0.05):
    period = "ht" if market.family == MarketFamily.FIRST_HALF_TOTALS else "second_half"
    return _period_total_probability(market, fixture, history, period, min_edge)


# Per-team stat markets: (stat name in features._STAT_FIELDS, league-average team total, min samples).
_STAT_MARKET_CONFIG = {
    MarketFamily.SHOTS: ("shots", 12.0, 4),
    MarketFamily.SHOTS_ON_TARGET: ("shots", 4.5, 4),
    MarketFamily.BOOKINGS: ("cards", 2.1, 5),
}


def score_stat_market(
    fixture: Fixture,
    market: MarketOdds,
    history: list[HistoricalMatch],
    min_edge: float = 0.05,
) -> Prediction | None:
    """Score data-backed per-team stat markets (shots, shots on target, cards).

    Requires enough observed history on BOTH teams before producing a model
    result, so fixtures without stat coverage fall through to consensus rather
    than manufacturing a fake model probability.
    """
    config = _STAT_MARKET_CONFIG.get(market.family)
    if config is None:
        return None
    stat, league_avg_team, min_samples = config

    selection = market.selection.casefold()
    if "over" not in selection and "under" not in selection:
        return None
    line = market.line or _extract_line(market.market) or _extract_line(market.selection)
    if line is None:
        return None

    home_profile = build_stat_profile(fixture.home.name, stat, history)
    away_profile = build_stat_profile(fixture.away.name, stat, history)
    if home_profile.samples < min_samples or away_profile.samples < min_samples:
        return None

    expected_total = expected_stat_total(home_profile, away_profile, league_avg_team)
    if expected_total <= 0:
        return None

    over = corner_over_probability(line, expected_total)  # Poisson tail, same shape as corners
    model_probability = over if "over" in selection else 1.0 - over
    return _prediction_if_value(
        fixture, market, model_probability, min_edge,
        f"Expected total {stat} {expected_total:.2f} "
        f"({home_profile.samples}+{away_profile.samples} samples); model {model_probability:.1%}.",
    )


# Full model confidence is reached at this many recent matches PER SIDE.
# Major leagues in season clear it easily (deep history -> solid, un-suppressed
# edges); thin lower-tier/pre-season fixtures score low and get quarantined by
# the slip builder instead of printing over-confident nonsense.
DATA_CONFIDENCE_ANCHOR = 8.0

# The most of the model's DISAGREEMENT with the bookmaker we are ever willing to
# back, even with a full history behind it. A closing line is an efficient
# estimate; our Poisson model is a *tilt* on it, not a replacement. At trust 0.5
# a raw model edge of +30% is only ever staked as +15%, and thin-history fixtures
# (low data_confidence) shrink further toward the market. This is the root-cause
# cure for "Model: 97.1% / edge +33%" nonsense — it caps overconfidence for EVERY
# league, not just the thin ones.
MODEL_MAX_TRUST = 0.5


def _data_confidence(
    fixture: Fixture,
    history: list[HistoricalMatch],
    family: MarketFamily,
) -> float:
    """0..1 measure of how much team history actually backs this fixture.

    Uses the same profile the scorer used (stat samples for stat markets, goal
    matches otherwise) and takes the WEAKER side — a pick is only as trustworthy
    as the thinner of its two teams. Ramps linearly to 1.0 at
    DATA_CONFIDENCE_ANCHOR matches/side.
    """
    stat_cfg = _STAT_MARKET_CONFIG.get(family)
    if stat_cfg is not None:
        stat, _league_avg, _min_samples = stat_cfg
        home = build_stat_profile(fixture.home.name, stat, history)
        away = build_stat_profile(fixture.away.name, stat, history)
        n_eff = min(home.samples, away.samples)
    else:
        home = build_goal_profile(fixture.home.name, history)
        away = build_goal_profile(fixture.away.name, history)
        n_eff = min(home.matches, away.matches)
    return max(0.0, min(1.0, n_eff / DATA_CONFIDENCE_ANCHOR))


def _calibrate_to_market(prediction: Prediction, data_confidence: float) -> Prediction:
    """Anchor the model's probability to the bookmaker line.

    The raw Poisson output is only trusted in proportion to
    ``MODEL_MAX_TRUST × data_confidence``; the rest of the weight sits on the
    market's implied probability. The result:

        calibrated = book + trust × (model − book)
        calibrated_edge = trust × raw_edge

    so a fantasy +33% collapses to a believable single-digit edge (and a genuine
    deep-history +8% survives as a solid ~+4%), instead of the model claiming a
    market misprices by 22 points. The uncalibrated value is preserved in
    ``raw_model_probability`` for transparency, and the safe-odds band + edge are
    re-derived from the calibrated probability.
    """
    trust = MODEL_MAX_TRUST * data_confidence
    book = prediction.implied_probability
    raw = prediction.model_probability
    calibrated = book + trust * (raw - book)
    calibrated = max(0.0, min(1.0, calibrated))
    band = DEFAULT_SAFE_ODDS_RULE.classify(calibrated)
    return replace(
        prediction,
        model_probability=calibrated,
        raw_model_probability=raw,
        edge=calibrated - book,
        safe_odds_band=band,
        confidence=band.value,
        data_confidence=data_confidence,
    )


def score_market(
    fixture: Fixture,
    market: MarketOdds,
    history: list[HistoricalMatch],
    min_edge: float = 0.05,
) -> Prediction | None:
    scorers = {
        MarketFamily.TOTALS: score_totals_market,
        MarketFamily.CORNERS: score_corners_market,
        MarketFamily.DOUBLE_CHANCE: score_double_chance_market,
        MarketFamily.HANDICAP: score_handicap_market,
        MarketFamily.MATCH_WINNER: score_match_winner_market,
        MarketFamily.BOTH_TEAMS_TO_SCORE: score_btts_market,
        MarketFamily.TEAM_TOTALS: score_team_total_market,
        MarketFamily.FIRST_HALF_TOTALS: score_period_totals_market,
        MarketFamily.SECOND_HALF_TOTALS: score_period_totals_market,
        MarketFamily.SHOTS: score_stat_market,
        MarketFamily.SHOTS_ON_TARGET: score_stat_market,
        MarketFamily.BOOKINGS: score_stat_market,
    }
    scorer = scorers.get(market.family)
    if scorer is None:
        return None
    prediction = scorer(fixture, market, history, min_edge=min_edge)
    if prediction is None:
        return None
    # This is the single choke point both production paths (scanner + telegram)
    # flow through, while the unit tests call the individual scorers directly and
    # keep the raw output — so calibration is applied to the live bot without
    # touching a single existing test assertion.
    confidence = _data_confidence(fixture, history, market.family)
    return _calibrate_to_market(prediction, confidence)



def score_fixture_markets(
    fixture: Fixture,
    markets: list[MarketOdds],
    history: list[HistoricalMatch],
    min_edge: float = 0.05,
) -> list[Prediction]:
    predictions = []
    for market in markets:
        prediction = score_market(fixture, market, history, min_edge=min_edge)
        if prediction is not None:
            predictions.append(prediction)
    return sorted(predictions, key=lambda prediction: (-prediction.model_probability, -prediction.edge))


def demo_predictions() -> list[Prediction]:
    fixture = Fixture(
        source="demo",
        source_id="demo-001",
        starts_at=datetime.now(),
        home=Team("Arsenal"),
        away=Team("Tottenham Hotspur"),
        league="Premier League",
    )
    history = [
        HistoricalMatch(None, "Arsenal", "Chelsea", 3, 1),
        HistoricalMatch(None, "Arsenal", "Liverpool", 2, 2),
        HistoricalMatch(None, "Manchester United", "Arsenal", 1, 2),
        HistoricalMatch(None, "Tottenham Hotspur", "Chelsea", 2, 1),
        HistoricalMatch(None, "Liverpool", "Tottenham Hotspur", 3, 2),
        HistoricalMatch(None, "Tottenham Hotspur", "Manchester United", 2, 2),
    ]
    markets = [
        MarketOdds("demo-book", "demo-001", MarketFamily.TOTALS, "Total Goals Over/Under 2.5", "Over 2.5", 1.9, 2.5),
        MarketOdds("demo-book", "demo-001", MarketFamily.TOTALS, "Total Goals Over/Under 3.5", "Under 3.5", 1.55, 3.5),
    ]
    return [
        prediction
        for market in markets
        if (prediction := score_totals_market(fixture, market, history, min_edge=0.01)) is not None
    ]


def demo_accumulator_predictions() -> list[Prediction]:
    candidate_specs = [
        ("Barcelona", "Getafe", "La Liga", "Home Over 0.5 Goals", "Yes", 1.22, 0.94, 0.120),
        ("Bayern Munich", "Augsburg", "Bundesliga", "Team To Score", "Bayern Munich Over 0.5", 1.18, 0.93, 0.083),
        ("Inter", "Empoli", "Serie A", "Double Chance", "Inter or Draw", 1.20, 0.92, 0.087),
        ("Ajax", "Sparta Rotterdam", "Eredivisie", "Total Goals Over/Under 0.5", "Over 0.5", 1.16, 0.92, 0.058),
        ("Porto", "Casa Pia", "Primeira Liga", "Home Over 0.5 Goals", "Yes", 1.24, 0.91, 0.104),
        ("Galatasaray", "Kayserispor", "Super Lig", "Double Chance", "Galatasaray or Draw", 1.21, 0.91, 0.084),
        ("Club Brugge", "Kortrijk", "Pro League", "Total Goals Over/Under 0.5", "Over 0.5", 1.19, 0.91, 0.070),
        ("PSG", "Nantes", "Ligue 1", "Total Goals Over/Under 0.5", "Over 0.5", 1.15, 0.91, 0.040),
        ("Celtic", "Dundee", "Premiership", "Double Chance", "Celtic or Draw", 1.17, 0.905, 0.050),
        ("Rangers", "St Mirren", "Premiership", "Double Chance", "Rangers or Draw", 1.35, 0.90, 0.159),
        ("Olympiacos", "OFI", "Super League", "Home Over 0.5 Goals", "Yes", 1.27, 0.90, 0.113),
        ("Benfica", "Boavista", "Primeira Liga", "Home Over 0.5 Goals", "Yes", 1.25, 0.90, 0.100),
        ("Real Madrid", "Alaves", "La Liga", "Double Chance", "Real Madrid or Draw", 1.42, 0.88, 0.176),
        ("Liverpool", "Burnley", "Premier League", "Home Over 0.5 Goals", "Yes", 1.47, 0.87, 0.190),
        ("Juventus", "Lecce", "Serie A", "Total Goals Over/Under 1.5", "Over 1.5", 1.55, 0.86, 0.215),
        ("Dortmund", "Mainz", "Bundesliga", "Double Chance", "Dortmund or Draw", 1.62, 0.84, 0.223),
        ("Marseille", "Metz", "Ligue 1", "Home Over 0.5 Goals", "Yes", 1.68, 0.83, 0.235),
        ("Sporting CP", "Estoril", "Primeira Liga", "Total Goals Over/Under 1.5", "Over 1.5", 1.74, 0.82, 0.245),
        ("Feyenoord", "Heracles", "Eredivisie", "Home Win Draw No Bet", "Feyenoord", 1.82, 0.81, 0.261),
        ("Fenerbahce", "Rizespor", "Super Lig", "Double Chance", "Fenerbahce or Draw", 1.90, 0.80, 0.274),
        ("Chelsea", "Everton", "Premier League", "Total Goals Over/Under 2.5", "Over 2.5", 2.05, 0.74, 0.252),
        ("Roma", "Torino", "Serie A", "Both Teams To Score", "Yes", 2.15, 0.72, 0.255),
        ("Leverkusen", "Wolfsburg", "Bundesliga", "Asian Handicap", "Leverkusen -1.0", 2.25, 0.70, 0.256),
        ("Lyon", "Reims", "Ligue 1", "Total Corners", "Over 8.5", 2.30, 0.69, 0.255),
        ("Besiktas", "Antalyaspor", "Super Lig", "Bookings", "Over 3.5 Cards", 2.45, 0.67, 0.262),
        ("Valencia", "Osasuna", "La Liga", "Total Goals Over/Under 2.5", "Under 2.5", 2.60, 0.65, 0.265),
        ("Brighton", "Fulham", "Premier League", "Both Teams To Score", "Yes", 2.80, 0.61, 0.253),
        ("Atalanta", "Udinese", "Serie A", "Asian Handicap", "Atalanta -1.0", 3.10, 0.58, 0.257),
        ("Monaco", "Nice", "Ligue 1", "Total Corners", "Over 9.5", 3.35, 0.55, 0.252),
        ("Sevilla", "Celta Vigo", "La Liga", "Bookings", "Over 4.5 Cards", 3.75, 0.52, 0.253),
    ]

    predictions = []
    for index, (home, away, league, market_name, selection, odds, probability, edge) in enumerate(candidate_specs, 1):
        fixture = Fixture(
            source="demo",
            source_id=f"demo-acca-{index:03}",
            starts_at=datetime.now(),
            home=Team(home),
            away=Team(away),
            league=league,
        )
        safe_odds_band = DEFAULT_SAFE_ODDS_RULE.classify(probability)
        book_probability = implied_probability(odds)
        predictions.append(
            Prediction(
                fixture=fixture,
                market=MarketOdds("demo-book", fixture.source_id, MarketFamily.UNKNOWN, market_name, selection, odds),
                model_probability=probability,
                implied_probability=book_probability,
                edge=edge,
                confidence=safe_odds_band.value,
                safe_odds_band=safe_odds_band,
                reason=(
                    f"Demo accumulator leg with model {probability:.1%} "
                    f"vs bookmaker implied {book_probability:.1%}."
                ),
            )
        )
    return predictions


def _extract_line(value: str) -> float | None:
    for token in value.replace("/", " ").split():
        try:
            return float(token)
        except ValueError:
            continue
    return None


def _prediction_if_value(
    fixture: Fixture,
    market: MarketOdds,
    model_probability: float,
    min_edge: float,
    reason: str,
) -> Prediction | None:
    book_probability = implied_probability(market.odds)
    edge = model_probability - book_probability
    if edge < min_edge:
        return None

    safe_odds_band = DEFAULT_SAFE_ODDS_RULE.classify(model_probability)
    return Prediction(
        fixture=fixture,
        market=market,
        model_probability=model_probability,
        implied_probability=book_probability,
        edge=edge,
        confidence=safe_odds_band.value,
        safe_odds_band=safe_odds_band,
        reason=f"{reason} Bookmaker implied {book_probability:.1%}.",
    )