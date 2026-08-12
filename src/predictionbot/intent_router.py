import json
import logging
import re
from datetime import date, timedelta
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
        
        # 🛡️ FORCE DATE: Python regex is more reliable than LLM for exact dates
        forced_date = self._force_extract_date(user_message)

        # 🛡️ FORCE RANGE/ODDS: the LLM mishandles "week" windows and the "1k"
        # shorthand, so extract them deterministically for a hard override below.
        forced_range = self._force_extract_range(user_message)
        forced_odds = self._extract_target_odds(user_message)

        intent = fallback_intent  # Default to fallback

        if self.api_key:
            # Try AI parsing
            try:
                ai_intent = self._ai_parse(user_message)
                if ai_intent and "error" not in ai_intent:
                    logger.info(f"✅ AI parsing successful: {ai_intent}")
                    # Merge with fallback to ensure all fields exist
                    intent = {**fallback_intent, **ai_intent}
            except Exception as e:
                logger.error(f"❌ AI parsing failed: {e}")
        else:
            logger.warning("⚠️ No API key - using keyword fallback")

        # 🚨 CRITICAL FIX: Force the date from regex if it exists in the message.
        # This prevents the AI from hallucinating today's date when the user
        # explicitly asks for tomorrow or a specific date like 2026-08-04.
        if forced_date:
            if intent.get("date") != forced_date:
                logger.info(f"📅 Date override: AI/Fallback said {intent.get('date')}, forcing regex match {forced_date}")
            intent["date"] = forced_date

        # A "week"/"weekend" request sets an explicit multi-day window. An
        # explicit YYYY-MM-DD in the message wins for the start date, and the
        # span is anchored to whichever start we end up using.
        if forced_range:
            default_start, span_days = forced_range
            if not forced_date:
                intent["date"] = default_start
            start = date.fromisoformat(intent["date"])
            end_iso = (start + timedelta(days=span_days)).isoformat()
            intent["end_date"] = end_iso
            logger.info(f"🗓️ Range override: scanning {intent['date']} .. {end_iso}")

        # Force the "k"-aware odds figure over any AI value (AI reads "1k" as 1.0).
        if forced_odds is not None and intent.get("target_odds") != forced_odds:
            logger.info(f"🎯 Odds override: AI/Fallback said {intent.get('target_odds')}, forcing {forced_odds}")
            intent["target_odds"] = forced_odds

        return intent

    def _force_extract_date(self, text: str) -> str | None:
        """Look for YYYY-MM-DD in the user's message."""
        match = re.search(r"(\d{4}-\d{2}-\d{2})", text)
        return match.group(1) if match else None

    def _extract_target_odds(self, text: str) -> float | None:
        """Parse a target-odds figure, honouring a 'k' thousands suffix.

        '10 odds' -> 10.0, '1k odds' -> 1000.0, '2.5k odd' -> 2500.0.
        The LLM regularly mishandles the 'k' shorthand, so this is also used
        as a hard override in parse_intent.
        """
        match = re.search(r"(\d+(?:\.\d+)?)\s*(k)?\s*odd", text.lower())
        if not match:
            return None
        value = float(match.group(1))
        if match.group(2):
            value *= 1000
        return value

    def _force_extract_range(self, text: str) -> tuple[str, int] | None:
        """Detect a range keyword and return (default_start_iso, span_days).

        - 'weekend' -> (upcoming Saturday, 1)  i.e. Saturday..Sunday.
        - 'week' / 'this week' -> (today, 6)    i.e. a full 7-day window.
        The caller anchors the span to an explicit start date when the user
        gave one; otherwise the default start is used. Returns None when no
        range keyword is present.
        """
        msg = text.lower()
        today = date.today()
        if "weekend" in msg:
            days_to_sat = (5 - today.weekday()) % 7
            saturday = today + timedelta(days=days_to_sat)
            return saturday.isoformat(), 1
        if "week" in msg:
            return today.isoformat(), 6
        return None

    def _ai_parse(self, user_message: str) -> dict:
        """AI-based intent parsing."""
        today_str = date.today().isoformat()
        tomorrow_str = (date.today() + timedelta(days=1)).isoformat()
        
        prompt = f"""Convert this betting request to JSON: "{user_message}"
Today's date is {today_str}. Tomorrow is {tomorrow_str}.

Rules:
- "5 odd", "10 odds", "20 odd accumulator" → target_odds: 5.0, 10.0, 20.0
- A "k" suffix means thousands: "1k odds" → 1000.0, "2.5k odd" → 2500.0
- "this week"/"for the week" → end_date = date + 6 days. "weekend" → date = upcoming Saturday, end_date = the Sunday
- No league mentioned → leagues: ["all"]
- "premier league" mentioned → leagues: ["premier_league"]
- Extract the exact date requested in YYYY-MM-DD format. 
  If they say "tomorrow", use {tomorrow_str}. 
  If they say "today" or no date is mentioned, use {today_str}. 
  If they provide a specific date, use that exact date.
- Always include: action: "scan", risk_progression: ["very_safe", "safe", "medium_risk"]

JSON format:
{{"action": "scan", "target_odds": 10.0, "leagues": ["all"], "market_family": "all", "date": "YYYY-MM-DD", "end_date": null, "risk_progression": ["very_safe", "safe", "medium_risk"]}}"""

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
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                return {"error": "Invalid JSON"}
        return {"error": "No JSON found"}
    
    def _keyword_parse(self, user_message: str) -> dict:
        """Keyword-based fallback parsing - NEVER fails."""
        today_str = date.today().isoformat()
        msg = user_message.lower()
        
        # Extract odds number (e.g., "5 odd", "10 odds", "20 odd accumulator",
        # "1k odds", "2.5k odd"). A trailing "k"/"K" multiplies by 1000.
        target_odds = self._extract_target_odds(msg)
        
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