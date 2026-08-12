import os
import sys
import requests

# Load your settings to get the API key
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))
from predictionbot.config import load_settings

settings = load_settings()
url = "https://v3.football.api-sports.io/fixtures?date=2026-07-04"
headers = {"x-apisports-key": settings.api_football_key}

print("🔍 Pinging API-Football directly...")
response = requests.get(url, headers=headers)

print("\n🚨 RAW API RESPONSE:")
print(response.json())