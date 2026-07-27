# telegram_bot.py
import asyncio
import logging
import os
import sys
import uuid
import datetime
from datetime import date, timedelta
from zoneinfo import ZoneInfo
from collections import defaultdict
from dotenv import load_dotenv

load_dotenv() # Load .env variables

sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from supabase import create_client, Client

from predictionbot.config import load_settings
from predictionbot.http import JsonHttpClient, HttpClientError
from predictionbot.intent_router import IntentRouter
from predictionbot.sources.bet9ja import Bet9jaClient, BET9JA_LEAGUES
from predictionbot.sources.openfootball import OpenFootballClient
from predictionbot.sources.api_football import ApiFootballProvider
from predictionbot.scanner import scan_events
from predictionbot.accumulator import build_progressive_accumulator
from predictionbot.domain import MarketFamily
from predictionbot.risk import SafeOddsBand
from predictionbot.matching import FixtureMatcher
from predictionbot.evaluator import evaluate_bet

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

settings = load_settings()
http = JsonHttpClient(settings.user_agent)
bet9ja = Bet9jaClient(http)
router = IntentRouter(http, api_key=settings.nvidia_api_key)

#  SUPABASE INITIALIZATION
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("Missing SUPABASE_URL or SUPABASE_KEY in .env")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

WAT = ZoneInfo("Africa/Lagos")

def format_wat_time(dt: datetime) -> str:
    if dt is None: return "TBD"
    if dt.tzinfo is None: dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(WAT).strftime("%b %d, %H:%M WAT")

CURRENT_SCOUT_PICKS = []
CURRENT_SCOUT_TOTAL_ODDS = 0.0
SHOWING_FULL_SLIP = False
SUMMARY_TEXT = ""
FULL_TEXT = ""

OPENFOOTBALL_LEAGUE_FILES = {
    "premier_league": "en.1.json", "championship": "en.2.json",
    "league_one": "en.3.json", "league_two": "en.4.json",
    "bundesliga": "de.1.json", "bundesliga_2": "de.2.json",
    "laliga": "es.1.json", "ligue_1": "fr.1.json",
    "ligue_2": "fr.2.json", "serie_a": "it.1.json",
}

def load_history_for_leagues(leagues: list[str]) -> list:
    client = OpenFootballClient(http)
    history = []
    if "all" in leagues: leagues = list(OPENFOOTBALL_LEAGUE_FILES.keys())
    for league in leagues:
        league_file = OPENFOOTBALL_LEAGUE_FILES.get(league)
        if not league_file: continue
        for season in "2023-24,2024-25".split(","):
            try: 
                matches = client.fetch_season(season.strip(), league_file)
                history.extend(matches)
            except HttpClientError: continue
    return history

def get_api_football_id(home: str, away: str, match_date: date) -> int | None:
    try:
        provider = ApiFootballProvider()
        matcher = FixtureMatcher()
        fixtures = provider.fixtures_by_date(match_date.isoformat())
        candidates = [type('Ext', (object,), {
            'id': str(f['fixture']['id']), 
            'home_team': f['teams']['home']['name'], 
            'away_team': f['teams']['away']['name'], 
            'date': datetime.datetime.fromisoformat(f['fixture']['date'].replace('Z', '+00:00'))
        })() for f in fixtures]
        match = matcher.match(home, away, datetime.datetime.combine(match_date, datetime.datetime.min.time()), candidates)
        return int(match.id) if match else None
    except Exception as e:
        logger.warning(f"Could not map {home} vs {away} to API-Football: {e}")
        return None

# 🌟 REPLACED SQLITE WITH SUPABASE INSERT
def save_bet_slip_to_db(chat_id: int, legs: list, slip_type: str):
    slip_id = str(uuid.uuid4())[:8]
    rows_to_insert = []
    for leg in legs:
        match_date = leg.fixture.starts_at.date() if leg.fixture.starts_at else date.today()
        af_id = get_api_football_id(leg.fixture.home.name, leg.fixture.away.name, match_date)
        rows_to_insert.append({
            "chat_id": chat_id,
            "slip_id": slip_id,
            "fixture_label": leg.fixture.label,
            "selection": f"{leg.market.market} - {leg.market.selection}",
            "odds": leg.market.odds,
            "match_time": leg.fixture.starts_at.isoformat() if leg.fixture.starts_at else "",
            "api_football_id": af_id,
            "status": "pending"
        })
    
    if rows_to_insert:
        supabase.table("user_bets").insert(rows_to_insert).execute()
        logger.info(f"💾 Saved {slip_type} slip {slip_id} to Supabase.")

async def update_history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔄 **Updating historical database...**\n\nThis may take 1-2 minutes. Please wait.")
    try:
        history = await asyncio.get_event_loop().run_in_executor(None, load_history_for_leagues, ["all"])
        await update.message.reply_text(f"✅ **Database Updated Successfully!**\n\n📚 Loaded **{len(history)}** total historical matches.")
    except Exception as e:
        logger.error(f"Update history failed: {e}")
        await update.message.reply_text(f"❌ **Failed to update database:**\n\n{str(e)}")

async def daily_history_update(context: ContextTypes.DEFAULT_TYPE):
    logger.info("🔄 Running daily historical database auto-update...")
    try:
        history = load_history_for_leagues(["all"])
        logger.info(f"✅ Daily update complete. Historical database now contains {len(history)} matches.")
    except Exception as e:
        logger.error(f"❌ Daily historical update failed: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 **PredictionBot Quant Assistant**\n\n"
        "I build mathematically diversified accumulators based on historical edges.\n\n"
        "Try: *'Give me a 10 odd accumulator for today'*\n"
        "Or type */update-history* to refresh the database.", 
        parse_mode="Markdown"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_msg = await update.message.reply_text("🔍 **Request Received!** Scanning historical data and markets... ⏳", parse_mode="Markdown")
    intent = router.parse_intent(update.message.text)
    if "error" in intent:
        await status_msg.edit_text("❌ Couldn't understand. Try: 'Give me 10 odds for today'")
        return
    try:
        result = await asyncio.to_thread(process_bet_request, intent, update.effective_chat.id)
        await status_msg.edit_text(result["message"], parse_mode="Markdown", reply_markup=result.get("keyboard"))
    except Exception as e:
        logger.error(e)
        await status_msg.edit_text(f"❌ Error: {str(e)}")

async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global SHOWING_FULL_SLIP, SUMMARY_TEXT, FULL_TEXT
    query = update.callback_query
    await query.answer()
    if not SUMMARY_TEXT and not FULL_TEXT:
        await query.edit_message_text("⚠️ Bot was restarted. Please request a new slip.", parse_mode="Markdown")
        return
    SHOWING_FULL_SLIP = not SHOWING_FULL_SLIP
    text = FULL_TEXT if SHOWING_FULL_SLIP else SUMMARY_TEXT
    btn_label = "🔼 Hide Details" if SHOWING_FULL_SLIP else "📋 View Full Slip Details"
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(btn_label, callback_data="toggle_slip")]])
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)

# 🌟 REPLACED SQLITE WITH SUPABASE FETCH & UPDATE
async def check_finished_matches(context: ContextTypes.DEFAULT_TYPE):
    logger.info("🔄 Checking for finished matches via Supabase...")
    
    # Fetch pending bets
    response = supabase.table("user_bets").select("*").eq("status", "pending").execute()
    pending = response.data
    
    if not pending:
        return

    provider = ApiFootballProvider()
    slips_to_notify = {}

    for row in pending:
        bet_id = row["id"]
        chat_id = row["chat_id"]
        slip_id = row["slip_id"]
        fixture = row["fixture_label"]
        selection = row["selection"]
        af_id = row["api_football_id"]
        match_time = row["match_time"]

        try:
            match_dt = datetime.datetime.fromisoformat(match_time)
            if match_dt.tzinfo is None: match_dt = match_dt.replace(tzinfo=ZoneInfo("UTC"))
            if datetime.datetime.now(ZoneInfo("UTC")) < match_dt + timedelta(hours=2):
                continue

            if not af_id:
                supabase.table("user_bets").update({"status": "void"}).eq("id", bet_id).execute()
                continue

            result = provider.get_fixture_result(str(af_id))
            if result and result["status"] in ["FT", "AET", "PEN"]:
                won = evaluate_bet(selection, result["home_score"], result["away_score"])
                new_status = "won" if won else "lost"
                supabase.table("user_bets").update({"status": new_status}).eq("id", bet_id).execute()
                
                if slip_id not in slips_to_notify:
                    slips_to_notify[slip_id] = {"chat_id": chat_id, "legs": [], "all_won": True}
                slips_to_notify[slip_id]["legs"].append({"fixture": fixture, "selection": selection, "score": f"{result['home_score']}-{result['away_score']}", "won": won})
                if not won:
                    slips_to_notify[slip_id]["all_won"] = False
        except Exception as e:
            logger.error(f"Error checking {fixture}: {e}")

    for slip_id, data in slips_to_notify.items():
        try:
            if data["all_won"]:
                msg = f"🎉 **SLIP WON!** ({slip_id})\n\n"
            else:
                msg = f"😔 **Slip Lost** ({slip_id})\n\n"
            for leg in data["legs"]:
                icon = "✅" if leg["won"] else "❌"
                msg += f"{icon} {leg['fixture']} ({leg['score']})\n   ➔ {leg['selection']}\n\n"
            await context.bot.send_message(chat_id=data["chat_id"], text=msg, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Failed to notify {data['chat_id']}: {e}")

def process_bet_request(intent: dict, chat_id: int) -> dict:
    global SUMMARY_TEXT, FULL_TEXT
    SHOWING_FULL_SLIP = False
    
    logger.info(f"📩 Processing request with intent: {intent}")
    target_date = date.fromisoformat(intent.get("date", date.today().isoformat()))
    leagues = intent.get("leagues", ["all"])
    target_odds = intent.get("target_odds")
    
    history = load_history_for_leagues(leagues)
    logger.info(f"📚 Loaded {len(history)} historical matches")
    
    try:
        if "all" in leagues:
            events = bet9ja.all_supported_events(target_date=target_date)
        else:
            events = []
            for league in leagues:
                if league in BET9JA_LEAGUES:
                    events.extend(bet9ja.league_events(league, target_date=target_date))
    except Exception as e:
        return {"success": False, "message": f"❌ Bet9ja API error: {str(e)}"}
    
    if not events:
        return {"success": False, "message": f"⚠️ No fixtures found for today ({target_date})."}

    market_families = {MarketFamily(intent["market_family"])} if intent.get("market_family") and intent["market_family"] != "all" else None
    result = scan_events(events, history=history, min_edge=0.03, market_families=market_families)
    
    if not result.predictions:
        return {"success": False, "message": "❌ Quant engine found no mathematical edges for today's fixtures."}
    
    fixture_best_picks = {}
    for pred in result.predictions:
        fid = pred.fixture.source_id
        if fid not in fixture_best_picks or pred.model_probability > fixture_best_picks[fid].model_probability:
            fixture_best_picks[fid] = pred
    unique_predictions = list(fixture_best_picks.values())

    estimated_legs = int((target_odds or 15) / 1.35) if target_odds else 15
    max_per_family = max(2, int(estimated_legs * 0.35))
    
    family_counts = defaultdict(int)
    diverse_predictions = []
    for pred in unique_predictions:
        family = pred.market.family
        if family_counts[family] < max_per_family:
            diverse_predictions.append(pred)
            family_counts[family] += 1
    
    diverse_predictions.sort(key=lambda p: p.model_probability, reverse=True)

    final_accumulator = None
    if target_odds and diverse_predictions:
        for risk_str in intent.get("risk_progression", ["very_safe", "safe", "medium_risk"]):
            acca = build_progressive_accumulator(
                predictions=diverse_predictions, target_odds=target_odds, 
                max_risk_band=SafeOddsBand(risk_str), max_legs=estimated_legs + 5
            )
            if acca.reached_target:
                final_accumulator = acca
                break

    if final_accumulator:
        text = f"✅ **Quant Target Reached!**\n🎯 **Total Odds:** {final_accumulator.total_odds:.2f}\n️ **Risk:** {final_accumulator.max_risk_band.value}\n📊 **Legs:** {len(final_accumulator.legs)}\n\n**The Slip:**\n"
        for i, leg in enumerate(final_accumulator.legs, 1):
            conf = leg.model_probability * 100
            text += f"{i}. *{format_wat_time(leg.fixture.starts_at)}* - {leg.fixture.label}\n   ➔ *{leg.market.selection}* ({conf:.0%} conf) @ {leg.market.odds:.2f}\n"
        
        save_bet_slip_to_db(chat_id, final_accumulator.legs, "quant")
        
        global SUMMARY_TEXT, FULL_TEXT
        SUMMARY_TEXT = text
        FULL_TEXT = text + "\n\n*All selections are based on historical mathematical edges.*"
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("ℹ️ View Edge Details", callback_data="toggle_slip")]])
        return {"success": True, "message": text, "keyboard": keyboard}

    max_possible_odds = 1.0
    for pred in diverse_predictions:
        max_possible_odds *= pred.market.odds
    
    future_opportunities = []
    for i in range(1, 8):
        future_date = target_date + timedelta(days=i)
        try:
            future_events = bet9ja.all_supported_events(target_date=future_date)
            if not future_events: continue
            future_result = scan_events(future_events, history=history, min_edge=0.03, market_families=market_families)
            edge_count = len(set(p.fixture.source_id for p in future_result.predictions))
            if edge_count >= 2:
                day_name = future_date.strftime("%A, %b %d")
                future_opportunities.append(f" **{day_name}**: {edge_count} edges available")
        except Exception: pass
    
    future_text = "\n\n🔮 **Upcoming Opportunities:**\n" + "\n".join(future_opportunities[:5]) if future_opportunities else "\n\n💡 *Check back when new leagues load their fixtures.*"

    return {
        "success": False, 
        "message": f"⚠️ **Target Not Reached.**\n\nThe Quant Engine found only **{len(diverse_predictions)}** valid edge(s) for today.\n\nWith the current available matches, the maximum safe odds we can build is approximately **{max_possible_odds:.2f}**.\n\n💡 *Try lowering your target (e.g., 2.0 or 3.0 odds).*{future_text}"
    }

def main() -> None:
    app = Application.builder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("update_history", update_history_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_click))
    
    try:
        from telegram.ext import JobQueue
        app.job_queue.run_repeating(check_finished_matches, interval=1800, first=10)
        app.job_queue.run_daily(daily_history_update, time=datetime.time(3, 0, tzinfo=WAT))
        logger.info("✅ Background jobs enabled.")
    except ImportError:
        logger.warning("️ Install job-queue: pip install 'python-telegram-bot[job-queue]'")

    print("🤖 Bot running in PURE QUANT MODE with Supabase Cloud DB...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()