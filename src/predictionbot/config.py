from __future__ import annotations

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    db_path: str = "predictionbot.sqlite3"
    user_agent: str = "Mozilla/5.0 PredictionBot/0.1"
    api_football_key: str | None = None
    sportmonks_api_token: str | None = None
    nvidia_api_key: str | None = None
    nvidia_model: str = "meta/llama-3.1-8b-instruct"
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"


def load_settings() -> Settings:
    return Settings(
        db_path=os.getenv("PREDICTIONBOT_DB", "predictionbot.sqlite3"),
        user_agent=os.getenv("PREDICTIONBOT_USER_AGENT", "Mozilla/5.0 PredictionBot/0.1"),
        api_football_key=os.getenv("API_FOOTBALL_KEY") or None,
        sportmonks_api_token=os.getenv("SPORTMONKS_API_TOKEN") or None,
        nvidia_api_key=os.getenv("NVIDIA_API_KEY") or None,
        nvidia_model=os.getenv("NVIDIA_MODEL", "meta/llama-3.1-8b-instruct"),
        nvidia_base_url=os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"),
    )
