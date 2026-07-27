from datetime import date

from predictionbot.sources.bet9ja import parse_league_events


def test_parse_league_events_extracts_fixture_and_basic_odds() -> None:
    payload = {
        "D": {
            "E": [
                {
                    "DS": "Brentford FC - Arsenal FC",
                    "GN": "Premier League",
                    "STARTDATE": "2021-08-13 20:00:00",
                    "GID": 135975,
                    "ID": 4467373,
                    "O": {
                        "S_1X2_1": 4.0,
                        "S_1X2_X": 3.75,
                        "S_1X2_2": 1.92,
                        "S_DC_1X": 1.89,
                        "S_DC_12": 1.3,
                        "S_DC_X2": 1.28,
                        "S_OU@2.5_O": 1.57,
                        "S_OU@2.5_U": 2.38,
                        "S_GGNG_Y": 2.53,
                        "S_GGNG_N": 1.52,
                        "S_AH@-1.5_1": 2.1,
                        "S_AH@-1.5_2": 1.7,
                    },
                }
            ]
        }
    }

    events = parse_league_events(payload)

    assert len(events) == 1
    assert events[0].fixture.source_id == "4467373"
    assert events[0].fixture.label == "Brentford FC vs Arsenal FC"
    assert events[0].fixture.starts_at.date() == date(2021, 8, 13)
    assert len(events[0].markets) == 12
    assert any(market.market == "Total Goals Over/Under 2.5" for market in events[0].markets)
    assert any(market.market == "Both Teams To Score" for market in events[0].markets)
    assert any(market.market == "Asian Handicap -1.5" for market in events[0].markets)
