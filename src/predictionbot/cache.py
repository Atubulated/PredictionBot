# src/predictionbot/cache.py
from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

CACHE_FILE = Path("xg_cache.json")

def load_cache() -> dict:
    if CACHE_FILE.exists():
        with open(CACHE_FILE, "r") as f:
            return json.load(f)
    return {}

def save_cache(data: dict) -> None:
    with open(CACHE_FILE, "w") as f:
        json.dump(data, f, indent=2)

def get_cached_xg(fixture_id: str, match_date: str) -> dict | None:
    """Returns cached xG data if it exists for today's date."""
    cache = load_cache()
    # Key format: "2024-05-20_123456"
    key = f"{match_date}_{fixture_id}"
    return cache.get(key)

def save_xg_to_cache(fixture_id: str, match_date: str, xg_data: dict) -> None:
    """Saves xG data to the local cache."""
    cache = load_cache()
    key = f"{match_date}_{fixture_id}"
    cache[key] = xg_data
    save_cache(cache)
    
def clear_old_cache(days_to_keep: int = 3) -> None:
    """Optional: Cleans up cache entries older than X days to keep file small."""
    # Implementation omitted for brevity, but good for long-term use
    pass