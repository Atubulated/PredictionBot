from __future__ import annotations

from datetime import datetime

from predictionbot.domain import Fixture, HistoricalMatch, MarketFamily, MarketOdds, Prediction, Team
from predictionbot.features import build_goal_profile
from predictionbot.models.corners import (
    calculate_expected_corners,
    corner_over_probability,
)
from predictionbot.models.goals import (
    estimate_expected_goals,
    estimate_expected_total,
    handicap_probability,
    outcome_probabilities,
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


def score_market(
    fixture: Fixture,
    market: MarketOdds,
    history: list[HistoricalMatch],
    min_edge: float = 0.05,
) -> Prediction | None:
    scorers = {
        MarketFamily.TOTALS: score_totals_market,
        MarketFamily.CORNERS: score_corners_market,  # <-- NEW: Wired up corners
        MarketFamily.DOUBLE_CHANCE: score_double_chance_market,
        MarketFamily.HANDICAP: score_handicap_market,
    }
    scorer = scorers.get(market.family)
    if scorer is None:
        return None
    return scorer(fixture, market, history, min_edge=min_edge)


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