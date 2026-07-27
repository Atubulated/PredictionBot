import sys
import os
from __future__ import annotations
from dataclasses import dataclass, replace

from predictionbot.domain import HistoricalMatch, MarketFamily, Prediction
from predictionbot.engine import score_fixture_markets
from predictionbot.sources.bet9ja import Bet9jaListedEvent


@dataclass(frozen=True)
class ScanResult:
    events_scanned: int
    markets_scored: int
    predictions: list[Prediction]


def scan_events(
    events: list[Bet9jaListedEvent],
    history: list[HistoricalMatch],
    min_edge: float = 0.05,
    market_families: set[MarketFamily] | None = None,
) -> ScanResult:
    predictions = []
    markets_scored = 0
    
    # 🌟 Initialize Supabase client once per scan for live form & xG lookup
    supabase_client = None
    try:
        from supabase import create_client
        supabase_url = os.getenv("SUPABASE_URL")
        supabase_key = os.getenv("SUPABASE_KEY")
        if supabase_url and supabase_key:
            supabase_client = create_client(supabase_url, supabase_key)
    except Exception:
        pass  # Fall back to historical-only if Supabase isn't available

    for event in events:
        markets = event.markets
        if market_families:
            markets = [market for market in markets if market.family in market_families]
            
        # 1. Get baseline predictions from historical engine
        scored = score_fixture_markets(event.fixture, markets, history, min_edge=min_edge)
        markets_scored += len(scored)
        
        # 2. 🌟 LIVE FORM & xG ADJUSTMENT
        if supabase_client and scored:
            home_name = event.fixture.home.name
            away_name = event.fixture.away.name
            
            try:
                # Fetch current form AND xG stats from Supabase
                home_stats = supabase_client.table('team_stats').select('wins, matches_played, xg_difference').eq('team_name', home_name).execute()
                away_stats = supabase_client.table('team_stats').select('wins, matches_played, xg_difference').eq('team_name', away_name).execute()
                
                if home_stats.data and away_stats.data:
                    home_wins = home_stats.data[0].get('wins', 0)
                    home_matches = max(1, home_stats.data[0].get('matches_played', 1))
                    home_form_strength = home_wins / home_matches
                    home_xg_diff = float(home_stats.data[0].get('xg_difference', 0) or 0)
                    
                    away_wins = away_stats.data[0].get('wins', 0)
                    away_matches = max(1, away_stats.data[0].get('matches_played', 1))
                    away_form_strength = away_wins / away_matches
                    away_xg_diff = float(away_stats.data[0].get('xg_difference', 0) or 0)
                    
                    adjusted_scored = []
                    for pred in scored:
                        # Base adjustment: 60% historical/model, 40% current win-rate form
                        adjusted_prob = (pred.model_probability * 0.6) + (home_form_strength * 0.4)
                        
                        # 🌟 xG Regression Adjustment:
                        # If home team has negative xG diff (lucky), penalize probability by up to 5%
                        # If home team has positive xG diff (unlucky), boost probability by up to 5%
                        xg_penalty = (home_xg_diff / 10.0) * 0.1  # Max +/- 0.05 (5%) adjustment
                        
                        adjusted_prob += xg_penalty
                        
                        # Keep probability bounded between 0.01 and 0.99
                        adjusted_prob = max(0.01, min(0.99, adjusted_prob))
                        
                        # Recalculate edge: (Odds * Probability) - 1
                        adjusted_edge = (pred.market.odds * adjusted_prob) - 1
                        
                        # Only keep if it still meets the minimum edge threshold
                        if adjusted_edge >= min_edge:
                            try:
                                new_pred = replace(pred, model_probability=adjusted_prob, edge=adjusted_edge)
                                adjusted_scored.append(new_pred)
                            except Exception:
                                pred.model_probability = adjusted_prob  # type: ignore
                                pred.edge = adjusted_edge  # type: ignore
                                adjusted_scored.append(pred)
                    
                    scored = adjusted_scored
            except Exception:
                pass  # Silently fall back to unadjusted predictions if form lookup fails
                
        predictions.extend(scored)

    # Sort by highest probability, then highest edge
    predictions = sorted(predictions, key=lambda prediction: (-prediction.model_probability, -prediction.edge))
    return ScanResult(events_scanned=len(events), markets_scored=markets_scored, predictions=predictions)