# PredictionBot

PredictionBot is a sports prediction and odds scanner foundation. The first target is football/soccer, with a practical v1 focused on fixtures, match stats, bookmaker odds, probability estimates, and value ranking.

This is not a guaranteed betting machine. The goal is to build a disciplined system that says:

- what the model thinks is likely
- what the bookmaker odds imply
- whether there is enough value to care
- why a pick was selected

## Safe Odds Rule

Safe odds are defined by our model probability, not by vibes or generic bookmaker labels:

- 90% and above model probability: `very_safe`
- 80% to 89.99% model probability: `safe`
- 65% to 79.99% model probability: `medium_risk`
- Below 65% model probability: `high_risk`

A pick can still have positive value while being below 80%. That means it may be interesting, but it should not be treated as one of the safer recommendations.

## V1 Scope

- Football only.
- Data sources prepared for Sofascore and OpenFootball.
- Odds source prepared for Bet9ja event markets.
- Markets started with totals, BTTS, double chance, and handicaps.
- Local SQLite storage.
- CLI-first workflow, so the engine can be tested before adding a dashboard or chat bot.

## Project Layout

```text
src/predictionbot/
  cli.py                  Command line entrypoint
  config.py               Environment and app settings
  domain.py               Core dataclasses
  engine.py               Prediction orchestration
  features.py             Feature calculations from historical data
  odds.py                 Odds math and market normalization helpers
  storage.py              SQLite persistence
  http.py                 Small JSON HTTP client
  models/
    goals.py              Goal/total-market probability model
  sources/
    sofascore.py          Sofascore fixture/stat adapter
    openfootball.py       Historical results adapter
    bet9ja.py             Bet9ja event-market adapter
```

## Quick Start

Run the demo scanner without external APIs:

```powershell
python -m predictionbot.cli demo
```

If running directly from the source tree without installing the package:

```powershell
$env:PYTHONPATH = "src"
python -m predictionbot.cli demo
```

Run tests:

```powershell
$env:PYTHONPATH = "src"
python -m pytest
```

Inspect one Bet9ja event's available markets:

```powershell
$env:PYTHONPATH = "src"
python -m predictionbot.cli bet9ja-event --event-id 123456
```

Filter by normalized market family:

```powershell
$env:PYTHONPATH = "src"
python -m predictionbot.cli bet9ja-event --event-id 123456 --family totals
```

Fetch Bet9ja listed events for one league/date. This is the first real-data step toward discovering event IDs automatically:

```powershell
$env:PYTHONPATH = "src"
python -m predictionbot.cli bet9ja-events --league premier_league --include-odds
```

Add `--date` when you want a specific day:

```powershell
$env:PYTHONPATH = "src"
python -m predictionbot.cli bet9ja-events --league premier_league --date 2026-08-21 --include-odds
```

Scan every currently supported Bet9ja league for a date:

```powershell
$env:PYTHONPATH = "src"
python -m predictionbot.cli bet9ja-events --all-leagues --date 2026-07-23 --include-odds
```

Build a demo accumulator from `very_safe` picks toward a target total odd:

```powershell
$env:PYTHONPATH = "src"
python -m predictionbot.cli acca-demo --target-odds 10
```

Build a larger accumulator by starting with `very_safe`, then adding `safe`, `medium_risk`, and finally `high_risk` only if needed:

```powershell
$env:PYTHONPATH = "src"
python -m predictionbot.cli acca-demo --target-odds 1000 --max-risk high_risk
```

Run the first real model-backed Bet9ja scan. This pulls live Bet9ja events, downloads OpenFootball history for the selected seasons, scores supported markets, and optionally tries to build an accumulator:

```powershell
$env:PYTHONPATH = "src"
python -m predictionbot.cli scan-bet9ja --league premier_league --history-seasons 2024-25,2025-26 --target-odds 10
```

Ask the NVIDIA/Llama reviewer to cross-check the scanner output:

```powershell
$env:NVIDIA_API_KEY = "your_nvidia_key"
$env:NVIDIA_MODEL = "meta/llama-3.1-8b-instruct"
$env:NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
$env:PYTHONPATH = "src"
python -m predictionbot.cli scan-bet9ja --league premier_league --target-odds 10 --ai-review
```

Filter by market family:

```powershell
$env:PYTHONPATH = "src"
python -m predictionbot.cli scan-bet9ja --league premier_league --market-family totals
```

The scanner only assigns confidence where a market has a model and enough historical team data. In v1, the model-backed markets are goals-derived: totals, double chance, and simple Asian handicap. Corners and bookings need richer event data before they should be trusted.

The AI reviewer is a second eye, not the calculator. It should challenge picks, suggest safer adjacent markets when they are present, and flag markets that need more data. It must not invent probabilities, lineups, injuries, or hidden stats.

## Interaction Layer

A Telegram bot should be treated as a user interface, not the prediction brain. A message like:

```text
Give me 10 very safe odds for today's matches
```

should call the same scanner and accumulator engine used by the CLI:

1. collect bookmaker fixtures and odds
2. collect historical/statistical data
3. score each supported market with the correct model
4. classify risk using the confidence thresholds
5. build a non-repeating accumulator toward the requested target odds
6. return a clear explanation, including when the requested target is not available

## Configuration

Copy `.env.example` to `.env` when we add real API credentials. For now the app is mostly config-free.

NVIDIA model APIs should be used for summaries, explanations, and report writing. The probability calculations should stay deterministic and testable.

## Data Source Notes

- Sofascore has useful fixture, team, event, statistics, lineup, and incident endpoints, but it is undocumented and may block basic HTTP clients.
- OpenFootball is useful for historical scores and fixtures. It is not enough for corners, bookings, shots, or Asian lines by itself.
- Bet9ja event endpoints can expose full match markets for a given event ID. The adapter is intentionally defensive because sportsbook payloads often change shape.

## Next Build Steps

1. Capture real sample JSON from one Bet9ja fixture endpoint.
2. Add a Bet9ja daily/league event list collector so the bot can scan all available matches for a day.
3. Add team-name matching between Sofascore fixtures and bookmaker fixtures.
4. Lock a normalized market schema for totals, BTTS, corners, cards, and handicaps.
5. Add a daily `scan-today` command that ranks live opportunities.
6. Use the accumulator builder to combine only `very_safe` picks toward requested total odds like 10, 20, or 100.
