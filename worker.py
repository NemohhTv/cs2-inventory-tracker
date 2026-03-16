"""
CS2 Inventory Price Tracker — Background Alerter
Polls prices for watchlist items and sends ntfy.sh alerts on significant changes.
Reads watchlist + settings from the shared /app/data volume.
"""
import json
import os
import re
import time

import requests
from dotenv import load_dotenv

load_dotenv()

DATA_DIR = os.getenv("DATA_DIR", "/app/data")
WATCHLIST_FILE = os.path.join(DATA_DIR, "watchlist.txt")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")

NTFY_TOPIC = (os.getenv("NTFY_TOPIC") or "").strip()
ALERT_THRESHOLD = float(os.getenv("ALERT_THRESHOLD", "0.05"))

STEAM_MARKET_PRICE_URL = "https://steamcommunity.com/market/priceoverview/"
CSFLOAT_LISTINGS_URL = "https://csfloat.com/api/v1/listings"

PRICE_DELAY_SEC = float(os.getenv("PRICE_DELAY_SEC", "3.0"))
MAX_ITEMS = int(os.getenv("CSFLOAT_MAX_ITEMS", "40"))
POLL_INTERVAL_SEC = int(os.getenv("WORKER_POLL_INTERVAL_SEC", "3600"))
RATE_LIMIT_BACKOFF_SEC = int(os.getenv("CSFLOAT_RATE_LIMIT_BACKOFF_SEC", "600"))

# ---------------------------------------------------------------------------
# Settings (shared with the web UI via /app/data)
# ---------------------------------------------------------------------------
def load_settings() -> dict:
    if os.path.isfile(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def get_csfloat_key() -> str:
    return (load_settings().get("csfloat_api_key") or os.getenv("CSFLOAT_API_KEY", "")).strip()


# ---------------------------------------------------------------------------
# Watchlist
# ---------------------------------------------------------------------------
def get_watchlist() -> list[str]:
    if os.path.isfile(WATCHLIST_FILE):
        try:
            with open(WATCHLIST_FILE, "r", encoding="utf-8") as f:
                items = [line.strip() for line in f if line.strip()]
            if items:
                return items
        except OSError:
            pass
    raw = os.getenv("WATCHLIST", "")
    return [x.strip() for x in raw.split(",") if x.strip()]


# ---------------------------------------------------------------------------
# Prices
# ---------------------------------------------------------------------------
def _parse_price_string(s: str) -> float | None:
    if not s:
        return None
    cleaned = re.sub(r"[^\d.,]", "", s)
    if "," in cleaned and "." in cleaned:
        if cleaned.rindex(",") > cleaned.rindex("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def fetch_steam_market_price(name: str) -> float | None:
    params = {"appid": "730", "currency": "1", "market_hash_name": name}
    try:
        r = requests.get(STEAM_MARKET_PRICE_URL, params=params, timeout=10)
        if r.status_code == 429:
            return None
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, ValueError):
        return None
    if not data.get("success"):
        return None
    return _parse_price_string(data.get("lowest_price", "")) or _parse_price_string(data.get("median_price", ""))


def fetch_csfloat_price(name: str) -> tuple[float | None, bool]:
    api_key = get_csfloat_key()
    params = {"market_hash_name": name, "limit": 1}
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        r = requests.get(CSFLOAT_LISTINGS_URL, params=params, headers=headers or None, timeout=10)
        if r.status_code == 429:
            return None, True
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, ValueError):
        return None, False
    listings = data if isinstance(data, list) else (data.get("listings") or data.get("data") or [])
    if not listings:
        return None, False
    first = listings[0] if isinstance(listings[0], dict) else {}
    price = first.get("price") or first.get("listing_price") or first.get("suggested_price")
    if price is None:
        return None, False
    return (float(price) / 100.0 if price > 1000 else float(price)), False


def get_price(name: str) -> tuple[float | None, bool]:
    """Try Steam Market, then CSFloat. Returns (price, was_rate_limited)."""
    price = fetch_steam_market_price(name)
    if price is not None:
        return price, False
    if get_csfloat_key():
        time.sleep(max(1.0, PRICE_DELAY_SEC))
        return fetch_csfloat_price(name)
    return None, False


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------
def send_ntfy(topic: str, title: str, message: str, emoji: str = "🔔") -> bool:
    if not topic:
        return False
    try:
        r = requests.post(
            f"https://ntfy.sh/{topic}",
            data=message.encode("utf-8"),
            headers={"Title": f"{emoji} {title}", "Content-Type": "text/plain; charset=utf-8"},
            timeout=10,
        )
        return r.status_code in (200, 201, 204)
    except requests.RequestException:
        return False


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def main():
    print(f"Worker starting. Poll interval: {POLL_INTERVAL_SEC}s, delay: {PRICE_DELAY_SEC}s")
    price_cache: dict[str, float] = {}

    while True:
        watchlist = get_watchlist()
        if not watchlist:
            print("Watchlist empty. Waiting 60s…")
            time.sleep(60)
            continue

        batch = watchlist[: max(1, MAX_ITEMS)]
        rate_limited = False
        print(f"Checking {len(batch)} items…")

        for i, name in enumerate(batch):
            if i > 0:
                time.sleep(max(1.0, PRICE_DELAY_SEC))

            price, was_429 = get_price(name)
            if was_429:
                rate_limited = True
                print(f"Rate limited; backing off {RATE_LIMIT_BACKOFF_SEC}s.")
                break
            if price is None:
                continue

            prev = price_cache.get(name)
            price_cache[name] = price
            if prev is None or prev <= 0:
                continue

            change = (price - prev) / prev
            if abs(change) < ALERT_THRESHOLD:
                continue

            emoji = "📈" if change > 0 else "📉"
            pct = f"+{change * 100:.1f}%" if change > 0 else f"{change * 100:.1f}%"
            title = f"CS2 price alert: {name[:50]}"
            msg = f"{emoji} {name}\n\n${prev:.2f} → ${price:.2f} ({pct})"
            ok = send_ntfy(NTFY_TOPIC, title, msg, emoji)
            print(f"{'Sent' if ok else 'FAILED'}: {name} {pct}")

        wait = RATE_LIMIT_BACKOFF_SEC if rate_limited else POLL_INTERVAL_SEC
        print(f"Sleeping {wait}s…")
        time.sleep(wait)


if __name__ == "__main__":
    main()
