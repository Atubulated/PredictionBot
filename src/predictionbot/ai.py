# src/predictionbot/ai.py
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from predictionbot.http import JsonHttpClient

logger = logging.getLogger(__name__)


# ==========================================
# 1. Existing Reviewer (for --ai-review)
# ==========================================
@dataclass(frozen=True)
class AiReview:
    enabled: bool
    model: str
    text: str


class NvidiaAiReviewer:
    def __init__(
        self, 
        http: JsonHttpClient, 
        api_key: str | None, 
        model: str = "meta/llama-3.1-8b-instruct",
        base_url: str = "https://integrate.api.nvidia.com/v1"
    ):
        self.http = http
        self.api_key = api_key
        self.model = model
        self.base_url = base_url

    def review_predictions(self, predictions: list[Any], target_odds: float | None = None) -> AiReview:
        if not self.api_key:
            return AiReview(enabled=False, model=self.model, text="NVIDIA API key not configured.")

        picks_summary = "\n".join([
            f"- {p.fixture.label}: {p.market.selection} @ {p.market.odds} (Model: {p.model_probability*100:.1f}%, Edge: {p.edge*100:.1f}%)"
            for p in predictions[:5]
        ])

        prompt = f"""You are an expert sports betting reviewer. Review these top model picks.
Do not invent new probabilities. Just provide a brief, expert commentary on the overall quality of these selections.
Target Odds: {target_odds or 'N/A'}

Picks:
{picks_summary}

Provide a short, professional review (2-3 sentences max)."""

        try:
            response = self.http.post_json(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                },
                payload={
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                    "max_tokens": 200
                }
            )
            text = response.get("choices", [{}])[0].get("message", {}).get("content", "No response from AI.")
            return AiReview(enabled=True, model=self.model, text=text.strip())
        except Exception as e:
            return AiReview(enabled=True, model=self.model, text=f"AI Review failed: {e}")


# ==========================================
# 2. New Analyst (for --triple-check and --ai-scout)
# ==========================================
@dataclass(frozen=True)
class AnalystVerdict:
    enabled: bool
    model: str
    verdict: str  # "APPROVE", "REVIEW", or "REJECT"
    reason: str


class NvidiaAiAnalyst:
    """
    Acts as the Triple-Check Analyst and AI Scout. 
    Reviews model predictions against external context and evaluates matches with weak historical data.
    """
    def __init__(
        self, 
        http: JsonHttpClient, 
        api_key: str | None, 
        model: str = "meta/llama-3.1-8b-instruct",
        base_url: str = "https://integrate.api.nvidia.com/v1"
    ):
        self.http = http
        self.api_key = api_key
        self.model = model
        self.base_url = base_url

    def triple_check(self, prediction: Any, external_stats: str = "N/A") -> AnalystVerdict:
        if not self.api_key:
            return AnalystVerdict(enabled=False, model=self.model, verdict="SKIP", reason="No API Key")

        prompt = f"""You are an expert sports betting risk analyst. You are the final triple-check before a bet is placed.
You do NOT calculate probabilities. You audit the mathematical model's output.

Analyze this prediction:
- Fixture: {prediction.fixture.label}
- Market: {prediction.market.market} ({prediction.market.selection})
- Model Probability: {prediction.model_probability * 100:.1f}%
- Bookmaker Odds: {prediction.market.odds}
- Edge: {prediction.edge * 100:.1f}%
- External Context (xG/Form): {external_stats}

Rules:
1. If Model Probability is >= 90% (very_safe) AND Edge is positive, normally APPROVE.
2. If the External Context heavily contradicts the model (e.g., Model says 95% Over 2.5 goals, but xG is only 1.2), output REJECT.
3. If the odds are too low for the risk (e.g., 1.05 odds for a 90% pick), output REVIEW.

Respond ONLY with a valid JSON object in this exact format:
{{"verdict": "APPROVE"|"REVIEW"|"REJECT", "reason": "Your brief analysis here"}}"""

        try:
            response = self.http.post_json(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                },
                payload={
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                    "max_tokens": 150
                }
            )
            
            raw_text = response.get("choices", [{}])[0].get("message", {}).get("content", "")
            
            # Extract JSON from potential markdown formatting
            match = re.search(r'\{.*?\}', raw_text, re.DOTALL)
            if match:
                data = json.loads(match.group(0))
                return AnalystVerdict(
                    enabled=True,
                    model=self.model,
                    verdict=data.get("verdict", "REVIEW").upper(),
                    reason=data.get("reason", "No reason provided")
                )
            return AnalystVerdict(enabled=True, model=self.model, verdict="ERROR", reason="Failed to parse JSON")
            
        except Exception as e:
            logger.error(f"AI Analyst failed: {e}")
            return AnalystVerdict(enabled=True, model=self.model, verdict="ERROR", reason=str(e))

    def scout_fixture(
        self, 
        home_team: str, 
        away_team: str, 
        league: str, 
        markets: list[dict]
    ) -> dict:
        """
        Uses AI to qualitatively analyze a match (especially friendlies/cups) 
        where historical statistical data is limited or unreliable.
        """
        if not self.api_key:
            return {"error": "NVIDIA API key not configured."}

        # Format markets for the prompt (limit to top 5 to save tokens)
        markets_str = "\n".join([
            f"- {m.get('market', 'Unknown')} ({m.get('selection', 'Unknown')}) @ {m.get('odds', 'N/A')}" 
            for m in markets[:5]
        ])
        
        prompt = f"""You are an expert quantitative football analyst and scout. 
Analyze the following pre-season or cup fixture where historical statistical data is limited or unreliable.

Fixture: {home_team} vs {away_team}
League/Competition: {league}

Available Bookmaker Markets & Odds:
{markets_str}

As a professional scout, evaluate this match considering:
1. Pre-season/Cup dynamics (likelihood of heavy squad rotation, fitness focus vs. competitive intensity).
2. Relative squad depth and quality disparity between the two clubs.
3. The implied probability of the provided odds.

Provide your analysis in STRICT JSON format only, with no markdown formatting, containing exactly these keys:
{{
  "recommended_market": "The single safest market from the list above",
  "recommended_selection": "The specific selection (e.g., 'Over 2.5', 'Home or Draw')",
  "estimated_probability": 0.85,
  "confidence_band": "safe" or "medium_risk" or "high_risk",
  "scout_reasoning": "A concise, professional 2-sentence explanation of why this is the best angle, factoring in pre-season dynamics."
}}"""

        try:
            response = self.http.post_json(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                },
                payload={
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                    "max_tokens": 300
                }
            )
            
            raw_text = response.get("choices", [{}])[0].get("message", {}).get("content", "")
            
            # Extract JSON from potential markdown formatting
            match = re.search(r'\{.*?\}', raw_text, re.DOTALL)
            if match:
                return json.loads(match.group(0))
            
            # Fallback if no curly braces found (sometimes LLMs just output raw JSON)
            try:
                return json.loads(raw_text.strip())
            except json.JSONDecodeError:
                return {"error": "Failed to parse AI response as JSON", "raw_response": raw_text}
                
        except Exception as e:
            logger.error(f"AI Scout failed: {e}")
            return {"error": str(e)}