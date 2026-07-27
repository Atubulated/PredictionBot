# src/predictionbot/intent_router.py
import json
import logging
import re
from datetime import date
from predictionbot.http import JsonHttpClient

logger = logging.getLogger(__name__)

class IntentRouter:
    def __init__(self, http: JsonHttpClient, api_key: str, model: str = "meta/llama-3.1-8b-instruct"):
        self.http = http
        self.api_key = api_key
        self.model = model
        self.base_url = "https://integrate.api.nvidia.com/v1"

    def parse_intent(self, user_message: str) -> dict:
        """Translates natural language into structured bot parameters."""
        logger.info(f"📩 Parsing user message: '{user_message}'")
        
        # 🛡️ FALLBACK: Keyword-based parsing (works even if AI fails)
        fallback_intent = self._keyword_parse(user_message)
        
        if not self.api_key:
            logger.warning("⚠️ No API key - using keyword fallback")
            return fallback_intent
        
        # Try AI parsing first
        try:
            ai_intent = self._ai_parse(user_message)
            if ai_intent and "error" not in ai_intent:
                logger.info(f"✅ AI parsing successful: {ai_intent}")
                # Merge with fallback to ensure all fields exist
                return {**fallback_intent, **ai_intent}
        except Exception as e:
            logger.error(f"❌ AI parsing failed: {e}")
        
        # Fallback to keyword parsing
        logger.warning(f"⚠️ Using keyword fallback: {fallback_intent}")
        return fallback_intent
    
    def _ai_parse(self, user_message: str) -> dict:
        """AI-based intent parsing."""
        today_str = date.today().isoformat()
        
        prompt = f"""Convert this betting request to JSON: "{user_message}"

Rules:
- "5 odd", "10 odds", "20 odd accumulator" → target_odds: 5.0, 10.0, 20.0
- No league mentioned → leagues: ["all"]
- "premier league" mentioned → leagues: ["premier_league"]
- Date not mentioned → date: "{today_str}"
- Always include: action: "scan", risk_progression: ["very_safe", "safe", "medium_risk"]

JSON format:
{{"action": "scan", "target_odds": 10.0, "leagues": ["all"], "market_family": "all", "date": "{today_str}", "risk_progression": ["very_safe", "safe", "medium_risk"]}}"""

        response = self.http.post_json(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            payload={
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "max_tokens": 200
            }
        )
        
        raw_text = response.get("choices", [{}])[0].get("message", {}).get("content", "{}")
        
        # Extract JSON
        match = re.search(r'\{.*?\}', raw_text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        return {"error": "No JSON found"}
    
    def _keyword_parse(self, user_message: str) -> dict:
        """Keyword-based fallback parsing - NEVER fails."""
        today_str = date.today().isoformat()
        msg = user_message.lower()
        
        # Extract odds number (e.g., "5 odd", "10 odds", "20 odd accumulator")
        odds_match = re.search(r'(\d+(?:\.\d+)?)\s*odd', msg)
        target_odds = float(odds_match.group(1)) if odds_match else None
        
        # Detect leagues
        leagues = ["all"]  # Default to ALL leagues
        if "premier league" in msg or "epl" in msg:
            leagues = ["premier_league"]
        elif "laliga" in msg or "la liga" in msg or "spanish" in msg:
            leagues = ["laliga"]
        elif "bundesliga" in msg or "german" in msg:
            leagues = ["bundesliga"]
        elif "serie a" in msg or "italian" in msg:
            leagues = ["serie_a"]
        elif "ligue 1" in msg or "french" in msg:
            leagues = ["ligue_1"]
        elif "mls" in msg or "american" in msg:
            leagues = ["mls_usa"]
        elif "brazil" in msg or "brasileiro" in msg:
            leagues = ["brasileiro_serie_a"]
        elif "fa cup" in msg:
            leagues = ["fa_cup"]
        
        # Detect market family
        market_family = "all"
        if "corner" in msg:
            market_family = "corners"
        elif "booking" in msg or "card" in msg:
            market_family = "bookings"
        elif "over" in msg or "under" in msg or "total" in msg:
            market_family = "totals"
        elif "btts" in msg or "both teams" in msg:
            market_family = "both_teams_to_score"
        
        # Detect date
        date_match = re.search(r'(\d{4}-\d{2}-\d{2})', msg)
        if date_match:
            date_str = date_match.group(1)
        elif "today" in msg:
            date_str = today_str
        elif "tomorrow" in msg:
            from datetime import timedelta
            date_str = (date.today() + timedelta(days=1)).isoformat()
        else:
            date_str = today_str
        
        return {
            "action": "scan",
            "target_odds": target_odds,
            "leagues": leagues,
            "market_family": market_family,
            "date": date_str,
            "risk_progression": ["very_safe", "safe", "medium_risk"]
        }