from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from types import SimpleNamespace

from predictionbot.ai import NvidiaAiAnalyst, NvidiaAiReviewer
from predictionbot.accumulator import build_progressive_accumulator
from predictionbot.config import load_settings
from predictionbot.domain import HistoricalMatch, MarketFamily
from predictionbot.engine import demo_accumulator_predictions, demo_predictions, score_totals_market
from predictionbot.http import HttpClientError, JsonHttpClient
from predictionbot.risk import SafeOddsBand
from predictionbot.scanner import scan_events
from predictionbot.sources.api_football import ApiFootballProvider
from predictionbot.sources.bet9ja import BET9JA_LEAGUES, Bet9jaClient
from predictionbot.sources.openfootball import OpenFootballClient
from predictionbot.sources.sofascore import SofascoreClient
from predictionbot.sources.sportmonks import SportmonksProvider
from predictionbot.stats import combine_fixture_stats
from predictionbot.storage import Repository
from predictionbot.cache import get_cached_xg, save_xg_to_cache
from predictionbot.matching import FixtureMatcher


OPENFOOTBALL_LEAGUE_FILES = {
    "premier_league": "en.1.json",
    "championship": "en.2.json",
    "league_one": "en.3.json",
    "league_two": "en.4.json",
    "bundesliga": "de.1.json",
    "bundesliga_2": "de.2.json",
    "laliga": "es.1.json",
    "ligue_1": "fr.1.json",
    "ligue_2": "fr.2.json",
    "serie_a": "it.1.json",
}


def main() -> None:
    parser = argparse.ArgumentParser(prog="predictionbot")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-db", help="Create local SQLite tables.")
    subparsers.add_parser("demo", help="Run a local demo prediction without external APIs.")

    acca_parser = subparsers.add_parser("acca-demo", help="Build a demo accumulator from very safe picks.")
    acca_parser.add_argument("--target-odds", type=float, default=10.0)
    acca_parser.add_argument("--max-legs", type=int, default=30)
    acca_parser.add_argument(
        "--max-risk",
        choices=[band.value for band in SafeOddsBand],
        default=SafeOddsBand.HIGH_RISK.value,
        help="Highest risk tier allowed if safer tiers cannot reach the target.",
    )

    schedule_parser = subparsers.add_parser("fixtures", help="Fetch scheduled fixtures from Sofascore.")
    schedule_parser.add_argument("--sport", default="football")
    schedule_parser.add_argument("--date", default=date.today().isoformat())
    schedule_parser.add_argument("--limit", type=int, default=20)

    stats_parser = subparsers.add_parser("stats-fixture", help="Fetch normalized stats for a specific fixture ID.")
    stats_parser.add_argument("--provider", required=True, choices=["api_football", "sportmonks"])
    stats_parser.add_argument("--fixture-id", required=True, help="Provider-specific fixture ID")
    stats_parser.add_argument(
        "--combine", 
        action="store_true", 
        help="Run through combine_fixture_stats to check for data conflicts"
    )

    pred_parser = subparsers.add_parser("stats-prediction", help="Fetch xG and form predictions for a fixture.")
    pred_parser.add_argument("--fixture-id", required=True, help="API-Football fixture ID")
    daily_fixtures_parser = subparsers.add_parser("daily-fixtures", help="Fetch ALL global fixtures for a specific date via API-Football.")
    daily_fixtures_parser.add_argument("--date", default=date.today().isoformat(), help="YYYY-MM-DD (defaults to today)")
    daily_fixtures_parser.add_argument("--limit", type=int, default=50, help="Max fixtures to display")

    bet9ja_parser = subparsers.add_parser("bet9ja-event", help="Fetch and normalize Bet9ja markets for one event.")
    bet9ja_parser.add_argument("--event-id", required=True)
    bet9ja_parser.add_argument("--limit", type=int, default=100)
    bet9ja_parser.add_argument(
        "--family",
        choices=[family.value for family in MarketFamily],
        help="Only show one normalized market family.",
    )

    bet9ja_events_parser = subparsers.add_parser("bet9ja-events", help="Fetch Bet9ja listed events by league/date.")
    bet9ja_events_parser.add_argument(
        "--league",
        choices=sorted(BET9JA_LEAGUES),
        default="premier_league",
        help="Bet9ja league to scan.",
    )
    bet9ja_events_parser.add_argument("--all-leagues", action="store_true", help="Scan all supported Bet9ja leagues.")
    bet9ja_events_parser.add_argument("--date", help="Optional YYYY-MM-DD filter. Omit to show upcoming events.")
    bet9ja_events_parser.add_argument("--limit", type=int, default=100)
    bet9ja_events_parser.add_argument("--include-odds", action="store_true")

    scan_parser = subparsers.add_parser("scan-bet9ja", help="Scan Bet9ja events with model-backed predictions.")
    scan_parser.add_argument("--league", choices=sorted(BET9JA_LEAGUES), default="premier_league")
    scan_parser.add_argument("--all-leagues", action="store_true", help="Scan all supported Bet9ja leagues.")
    scan_parser.add_argument("--date", help="Optional YYYY-MM-DD fixture filter. Omit to scan upcoming events.")
    scan_parser.add_argument("--history-seasons", default="2024-25,2025-26")
    scan_parser.add_argument("--history-file", action="append", default=[])
    scan_parser.add_argument("--min-edge", type=float, default=0.05)
    scan_parser.add_argument("--limit", type=int, default=25)
    scan_parser.add_argument(
        "--market-family",
        action="append",
        choices=[family.value for family in MarketFamily],
        help="Restrict scan to one or more market families. Repeat for multiple.",
    )
    scan_parser.add_argument("--target-odds", type=float)
    scan_parser.add_argument("--max-legs", type=int, default=30)
    scan_parser.add_argument("--ai-review", action="store_true", help="Ask the configured NVIDIA model to review picks.")
    scan_parser.add_argument(
        "--max-risk",
        choices=[band.value for band in SafeOddsBand],
        default=SafeOddsBand.HIGH_RISK.value,
        help="Highest risk tier allowed for the accumulator.",
    )
    scan_parser.add_argument("--triple-check", action="store_true", help="Run the AI Analyst triple-check on top picks.")
    scan_parser.add_argument("--live-xg", action="store_true", help="Enrich top picks with live API-Football xG data.")

    scout_parser = subparsers.add_parser("ai-scout", help="Use AI to qualitatively analyze matches with weak historical data (e.g., friendlies).")
    scout_parser.add_argument("--league", required=True, help="League to scout (e.g., 'fa_cup' for friendlies)")
    scout_parser.add_argument("--date", help="Optional YYYY-MM-DD filter. Defaults to today.")
    scout_parser.add_argument("--limit", type=int, default=10, help="Max matches for AI to analyze (to save API tokens)")

    args = parser.parse_args()
    settings = load_settings()

    if args.command == "init-db":
        Repository(settings.db_path).initialize()
        print(f"Initialized database at {settings.db_path}")
        return

    if args.command == "demo":
        predictions = demo_predictions()
        print_predictions(predictions)
        return

    if args.command == "acca-demo":
        predictions = demo_accumulator_predictions()
        accumulator = build_progressive_accumulator(
            predictions=predictions,
            target_odds=args.target_odds,
            max_risk_band=SafeOddsBand(args.max_risk),
            max_legs=args.max_legs,
        )
        print_accumulator(accumulator)
        return

    if args.command == "fixtures":
        http = JsonHttpClient(settings.user_agent)
        sofascore = SofascoreClient(http)
        try:
            fixtures = sofascore.scheduled_events(args.sport, args.date)
        except HttpClientError as exc:
            raise SystemExit(str(exc)) from exc

        for fixture in fixtures[: args.limit]:
            starts_at = fixture.starts_at.isoformat() if fixture.starts_at else "unknown time"
            print(f"{fixture.source_id} | {starts_at} | {fixture.league or 'Unknown league'} | {fixture.label}")
        return

    if args.command == "stats-fixture":
        if args.provider == "api_football":
            provider = ApiFootballProvider()
        else:
            provider = SportmonksProvider()
        
        print(f"Fetching stats for {args.provider} fixture {args.fixture_id}...")
        try:
            stats = provider.fixture_stats(args.fixture_id)
            print(f"Successfully fetched {len(stats.team_stats)} team stat entries.\n")
            
            rows = [
                {
                    "subject": stat.subject,
                    "code": stat.code.value,
                    "value": stat.value,
                    "raw_name": stat.raw_name,
                    "provider": stat.provider,
                }
                for stat in stats.team_stats
            ]
            print(json.dumps({"fixture_id": stats.fixture_id, "stats": rows}, indent=2))
            
            if args.combine:
                combined = combine_fixture_stats(stats.fixture_id, [stats])
                if combined.conflicts:
                    print("\n⚠️ Conflicts detected:")
                    print(json.dumps(combined.conflicts, indent=2))
                else:
                    print("\n✅ No conflicts detected in combined stats.")
        except ValueError as e:
            raise SystemExit(f"❌ Configuration Error: {e}\n   Please set the appropriate API key in your .env file.") from e
        except Exception as e:
            raise SystemExit(f"❌ Failed to fetch stats: {e}") from e
        return

    if args.command == "stats-prediction":
        from predictionbot.risk import DEFAULT_SAFE_ODDS_RULE

        provider = ApiFootballProvider()
        print(f"Fetching predictions for API-Football fixture {args.fixture_id}...")
        try:
            data = provider.fixture_predictions(args.fixture_id)
            if not data:
                print("❌ No predictions found for this fixture.")
                return 1

            predictions = data.get("predictions", {})
            winner = predictions.get("winner", {})
            percent_raw = predictions.get("percent", {})

            probs = {}
            for key, val in percent_raw.items():
                try:
                    probs[key] = float(str(val).replace('%', '')) / 100.0
                except ValueError:
                    probs[key] = 0.0

            best_outcome = max(probs, key=probs.get) if probs else "unknown"
            best_prob = probs.get(best_outcome, 0.0)
            safe_odds_band = DEFAULT_SAFE_ODDS_RULE.classify(best_prob)

            print(json.dumps({
                "fixture_id": args.fixture_id,
                "predicted_winner": winner.get("name") if winner else "Draw/Unknown",
                "probabilities": percent_raw,
                "best_outcome": best_outcome,
                "confidence_percent": round(best_prob * 100, 2),
                "safe_odds_band": safe_odds_band.value,
                "advice": predictions.get("advice", "No advice"),
            }, indent=2))
            return 0
        except Exception as e:
            raise SystemExit(f"❌ Error: {e}") from e

    if args.command == "daily-fixtures":
        provider = ApiFootballProvider()
        print(f"Fetching global fixtures for {args.date}...")
        try:
            fixtures = provider.fixtures_by_date(args.date)
            if not fixtures:
                print("❌ No fixtures found for this date.")
                return 1

            print(f"✅ Found {len(fixtures)} total fixtures. Showing first {args.limit}:\n")
            for fx in fixtures[:args.limit]:
                home = fx['teams']['home']['name']
                away = fx['teams']['away']['name']
                league = fx['league']['name']
                time = fx['fixture']['date'].split('T')[1][:5]  # Extract HH:MM
                print(f"[{time}] {league:20} | {home} vs {away}")

            return 0
        except Exception as e:
            raise SystemExit(f"❌ Error: {e}") from e

    if args.command == "bet9ja-event":
        http = JsonHttpClient(settings.user_agent)
        bet9ja = Bet9jaClient(http)
        try:
            markets = bet9ja.event_markets(args.event_id)
        except HttpClientError as exc:
            raise SystemExit(str(exc)) from exc

        if args.family:
            markets = [market for market in markets if market.family == MarketFamily(args.family)]
        print_markets(markets[: args.limit])
        return

    if args.command == "bet9ja-events":
        http = JsonHttpClient(settings.user_agent)
        bet9ja = Bet9jaClient(http)
        target_date = date.fromisoformat(args.date) if args.date else None
        try:
            if args.all_leagues:
                events = bet9ja.all_supported_events(target_date=target_date)
            else:
                events = bet9ja.league_events(args.league, target_date=target_date)
        except (HttpClientError, ValueError) as exc:
            raise SystemExit(str(exc)) from exc

        print_bet9ja_events(events[: args.limit], include_odds=args.include_odds)
        return

    if args.command == "scan-bet9ja":
        http = JsonHttpClient(settings.user_agent)
        bet9ja = Bet9jaClient(http)
        target_date = date.fromisoformat(args.date) if args.date else None
        try:
            if args.all_leagues:
                events = bet9ja.all_supported_events(target_date=target_date)
            else:
                events = bet9ja.league_events(args.league, target_date=target_date)
            history = load_history(
                http=http,
                league=args.league,
                seasons=args.history_seasons,
                history_files=args.history_file,
                all_leagues=args.all_leagues,
            )
        except (HttpClientError, ValueError) as exc:
            raise SystemExit(str(exc)) from exc

        market_families = {MarketFamily(value) for value in args.market_family or []} or None
        result = scan_events(events, history, min_edge=args.min_edge, market_families=market_families)
        
        # ==========================================
        # NEW: Smart Pre-Filter + Cache + Live xG Pipeline
        # ==========================================
        if args.live_xg and settings.api_football_key:
            print("\n🔄 Starting Smart xG Pipeline (Pre-filter ➔ Cache ➔ Fetch)...")
            af_provider = ApiFootballProvider()
            matcher = FixtureMatcher()
            
            scan_date_str = target_date.isoformat() if target_date else date.today().isoformat()
            
            # 1. PRE-FILTER: Find matches where historical model already shows >= 80% probability
            promising_candidates = [p for p in result.predictions if p.model_probability >= 0.80]
            print(f"   Found {len(promising_candidates)} promising candidates (>= 80% historical prob).")
            
            # 2. CHECK CACHE & FETCH: Only hit the API for matches we don't have cached for today
            missing_candidates = []
            for pred in promising_candidates:
                cached = get_cached_xg(pred.fixture.source_id, scan_date_str)
                if not cached:
                    missing_candidates.append(pred)
                    
            print(f"   Need to fetch live xG for {len(missing_candidates)} matches (Cache hit for {len(promising_candidates) - len(missing_candidates)}).")
            
            # 3. FETCH LIVE XG (Respecting API limits)
            daily_fixtures_raw = af_provider.fixtures_by_date(scan_date_str)
            candidates_for_matcher = []
            for fx in daily_fixtures_raw:
                candidates_for_matcher.append(type('ExternalFixture', (object,), {
                    'id': str(fx['fixture']['id']),
                    'home_team': fx['teams']['home']['name'],
                    'away_team': fx['teams']['away']['name'],
                    'date': datetime.fromisoformat(fx['fixture']['date'].replace('Z', '+00:00')),
                    'league': fx['league']['name']
                })())
                
            fetched_count = 0
            for pred in missing_candidates:
                if fetched_count >= 80: # Safety limit to protect free tier
                    print("   ⚠️ Reached daily API fetch limit (80). Stopping fetch.")
                    break
                    
                match = matcher.match(
                    local_home=pred.fixture.home.name,
                    local_away=pred.fixture.away.name,
                    local_date=pred.fixture.starts_at,
                    candidates=candidates_for_matcher
                )
                
                if match:
                    pred_data = af_provider.fixture_predictions(match.id)
                    if pred_data:
                        goals = pred_data.get("predictions", {}).get("goals", {})
                        try:
                            home_xg = float(goals.get("home", 0))
                            away_xg = float(goals.get("away", 0))
                            xg_payload = {"home": home_xg, "away": away_xg}
                            save_xg_to_cache(pred.fixture.source_id, scan_date_str, xg_payload)
                            fetched_count += 1
                        except (ValueError, TypeError):
                            pass
                            
            print(f"   ✅ Fetched and cached {fetched_count} new xG profiles.\n")
            
            # 4. BLEND & RE-SCORE: Update predictions with live xG
            refined_predictions = []
            for pred in result.predictions:
                cached_xg = get_cached_xg(pred.fixture.source_id, scan_date_str)
                live_xg_tuple = None
                if cached_xg:
                    live_xg_tuple = (cached_xg["home"], cached_xg["away"])
                    
                # Re-score using the blended logic in engine.py
                if pred.market.family.value == "totals":
                    new_pred = score_totals_market(
                        fixture=pred.fixture,
                        market=pred.market,
                        history=history,
                        min_edge=args.min_edge,
                        live_xg=live_xg_tuple
                    )
                    if new_pred:
                        refined_predictions.append(new_pred)
                else:
                    # Keep non-totals predictions as-is for now
                    refined_predictions.append(pred)
                    
            # Sort by edge/probability again
            refined_predictions.sort(key=lambda p: (-p.model_probability, -p.edge))
            
            result = SimpleNamespace(
                events_scanned=result.events_scanned,
                markets_scored=result.markets_scored,
                predictions=refined_predictions
            )
        # ==========================================
        
        # --- Filter predictions by max_risk BEFORE printing ---
        max_risk_band = SafeOddsBand(args.max_risk)
        
        if max_risk_band == SafeOddsBand.VERY_SAFE:
            allowed_bands = {SafeOddsBand.VERY_SAFE}
        elif max_risk_band == SafeOddsBand.SAFE:
            allowed_bands = {SafeOddsBand.VERY_SAFE, SafeOddsBand.SAFE}
        elif max_risk_band == SafeOddsBand.MEDIUM_RISK:
            allowed_bands = {SafeOddsBand.VERY_SAFE, SafeOddsBand.SAFE, SafeOddsBand.MEDIUM_RISK}
        else:
            allowed_bands = set(SafeOddsBand) # HIGH_RISK allows everything
            
        filtered_predictions = [p for p in result.predictions if p.safe_odds_band in allowed_bands]
        
        filtered_result = SimpleNamespace(
            events_scanned=result.events_scanned,
            markets_scored=result.markets_scored,
            predictions=filtered_predictions
        )
        print_scan_result(filtered_result, limit=args.limit)
        # -----------------------------------------------------------
        
        if args.target_odds:
            accumulator = build_progressive_accumulator(
                predictions=filtered_predictions,
                target_odds=args.target_odds,
                max_risk_band=max_risk_band,
                max_legs=args.max_legs,
            )
            print_accumulator(accumulator)
            
        if args.triple_check:
            analyst = NvidiaAiAnalyst(
                http=http,
                api_key=settings.nvidia_api_key,
                model=settings.nvidia_model,
                base_url=settings.nvidia_base_url,
            )
            
            print("\n🔍 Running AI Analyst Triple-Check on top 5 safe picks...")
            for pred in filtered_predictions[:5]: 
                verdict = analyst.triple_check(pred, external_stats="Live xG blended" if args.live_xg else "Historical only")
                print(f"  - {pred.fixture.label} ({pred.market.selection}): [{verdict.verdict}] - {verdict.reason}")
        return

    if args.command == "ai-scout":
        http = JsonHttpClient(settings.user_agent)
        bet9ja = Bet9jaClient(http)
        target_date = date.fromisoformat(args.date) if args.date else date.today()
        
        print(f"🔍 AI Scout activated for {args.league} on {target_date}...")
        
        try:
            events = bet9ja.league_events(args.league, target_date=target_date)
        except Exception as exc:
            raise SystemExit(f"❌ Failed to fetch events: {exc}") from exc
            
        if not events:
            print("⚠️ No events found for this league and date.")
            return

        analyst = NvidiaAiAnalyst(
            http=http,
            api_key=settings.nvidia_api_key,
            model=settings.nvidia_model,
            base_url=settings.nvidia_base_url,
        )

        print(f"🤖 Sending {min(len(events), args.limit)} matches to AI Analyst for qualitative scouting...\n")
        
        scout_results = []
        for event in events[:args.limit]:
            fixture = event.fixture
            print(f"Scouting: {fixture.label}...")
            
            # Gather available markets for this fixture
            available_markets = [
                {"market": m.market, "selection": m.selection, "odds": m.odds}
                for m in event.markets if m.family in [MarketFamily.TOTALS, MarketFamily.DOUBLE_CHANCE]
            ]
            
            if not available_markets:
                print("  ⏭️  Skipped: No suitable markets (Totals/Double Chance) found.")
                continue
                
            # Call the AI Scout
            result = analyst.scout_fixture(
                home_team=fixture.home.name,
                away_team=fixture.away.name,
                league=fixture.league or args.league,
                markets=available_markets
            )
            
            if "error" not in result:
                scout_results.append({
                    "fixture": fixture.label,
                    "league": fixture.league,
                    "ai_recommendation": result
                })
                print(f"  ✅ AI Pick: {result['recommended_market']} -> {result['recommended_selection']} ({result['estimated_probability']:.0%} prob)")
            else:
                print(f"  ❌ AI Error: {result['error']}")
                
        print("\n" + "="*80)
        print("📋 FINAL AI SCOUT REPORT")
        print("="*80)
        print(json.dumps(scout_results, indent=2))
        return
    
def print_predictions(predictions) -> None:
    rows = [
        {
            "fixture": prediction.fixture.label,
            "market": prediction.market.market,
            "selection": prediction.market.selection,
            "odds": prediction.market.odds,
            "model_probability": round(prediction.model_probability, 4),
            "confidence_percent": round(prediction.model_probability * 100, 2),
            "implied_probability": round(prediction.implied_probability, 4),
            "edge": round(prediction.edge, 4),
            "confidence": prediction.confidence,
            "safe_odds": prediction.is_safe_odds,
            "safe_odds_band": prediction.safe_odds_band.value,
            "fair_odds": prediction.fair_odds,
            "reason": prediction.reason,
        }
        for prediction in predictions
    ]
    print(json.dumps(rows, indent=2))


def print_markets(markets) -> None:
    rows = [
        {
            "bookmaker": market.bookmaker,
            "fixture_id": market.fixture_id,
            "family": market.family.value,
            "market": market.market,
            "selection": market.selection,
            "line": market.line,
            "odds": market.odds,
        }
        for market in markets
    ]
    print(json.dumps(rows, indent=2))


def print_accumulator(accumulator) -> None:
    rows = {
        "target_odds": accumulator.target_odds,
        "total_odds": accumulator.total_odds,
        "reached_target": accumulator.reached_target,
        "max_risk_allowed": accumulator.max_risk_band.value,
        "risk_bands_used": accumulator.risk_bands_used,
        "combined_probability": accumulator.combined_probability,
        "message": (
            "Target reached."
            if accumulator.reached_target
            else "Target not possible with the available non-repeating matches and selected risk limit."
        ),
        "legs": [
            {
                "fixture": leg.fixture.label,
                "market": leg.market.market,
                "selection": leg.market.selection,
                "odds": leg.market.odds,
                "confidence_percent": round(leg.model_probability * 100, 2),
                "safe_odds_band": leg.safe_odds_band.value,
                "edge": round(leg.edge, 4),
            }
            for leg in accumulator.legs
        ],
    }
    print(json.dumps(rows, indent=2))


def print_bet9ja_events(events, include_odds: bool = False) -> None:
    rows = []
    for event in events:
        fixture = event.fixture
        row = {
            "event_id": fixture.source_id,
            "starts_at": fixture.starts_at.isoformat() if fixture.starts_at else None,
            "league": fixture.league,
            "fixture": fixture.label,
        }
        if include_odds:
            row["odds"] = [
                {
                    "market": market.market,
                    "selection": market.selection,
                    "odds": market.odds,
                    "family": market.family.value,
                }
                for market in event.markets
            ]
        rows.append(row)
    print(json.dumps(rows, indent=2))


def print_ai_review(review) -> None:
    print(
        json.dumps(
            {
                "ai_review": {
                    "enabled": review.enabled,
                    "model": review.model,
                    "text": review.text,
                }
            },
            indent=2,
        )
    )


def print_scan_result(result, limit: int) -> None:
    rows = {
        "events_scanned": result.events_scanned,
        "markets_scored": result.markets_scored,
        "predictions_returned": min(len(result.predictions), limit),
        "predictions": [
            {
                "fixture": prediction.fixture.label,
                "starts_at": prediction.fixture.starts_at.isoformat() if prediction.fixture.starts_at else None,
                "league": prediction.fixture.league,
                "market": prediction.market.market,
                "selection": prediction.market.selection,
                "odds": prediction.market.odds,
                "confidence_percent": round(prediction.model_probability * 100, 2),
                "safe_odds_band": prediction.safe_odds_band.value,
                "edge": round(prediction.edge, 4),
                "reason": prediction.reason,
            }
            for prediction in result.predictions[:limit]
        ],
    }
    print(json.dumps(rows, indent=2))


def load_history(
    http: JsonHttpClient,
    league: str,
    seasons: str,
    history_files: list[str],
    all_leagues: bool = False,
) -> list[HistoricalMatch]:
    client = OpenFootballClient(http)
    history = []
    for history_file in history_files:
        history.extend(client.load_file(history_file))

    selected_leagues = sorted(OPENFOOTBALL_LEAGUE_FILES) if all_leagues else [league]
    selected_seasons = [season.strip() for season in seasons.split(",") if season.strip()]
    for selected_league in selected_leagues:
        league_file = OPENFOOTBALL_LEAGUE_FILES.get(selected_league)
        if league_file is None:
            continue
        for season in selected_seasons:
            try:
                history.extend(client.fetch_season(season, league_file))
            except HttpClientError:
                continue
    return history


if __name__ == "__main__":
    main()