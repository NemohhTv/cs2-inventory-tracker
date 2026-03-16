"""
CS2 Inventory Price Tracker - Background Alerter
Polls CSFloat prices for watchlist items and sends ntfy.sh alerts on significant changes.
Reads watchlist from shared file (same as UI) or from WATCHLIST env.
"""
import os
import time
import requests
from dotenv import load_dotenv

load_dotenv()

DATA_DIR = os.getenv("DATA_DIR", "/app/data")
WATCHLIST_FILE = os.path.join(DATA_DIR, "watchlist.txt")

NTFY_TOPIC = (os.getenv("NTFY_TOPIC") or "").strip()
ALERT_THRESHOLD = float(os.getenv("ALERT_THRESHOLD", "0.05"))
CSFLOAT_API_KEY = os.getenv("CSFLOAT_API_KEY", "").strip()
CSFLOAT_LISTINGS_URL = "https://csfloat.com/api/v1/listings"
CSFLOAT_DELAY_SEC = 1.5
POLL_INTERVAL_SEC = 1800  # 30 minutes


def get_watchlist() -> list[str]:
    """Read watchlist from file (one item per line) if it exists, else from env."""
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


def fetch_csfloat_price(market_hash_name: str) -> float | None:
    """Fetch lowest listing price (USD) for one item. Returns None on error."""
    params = {"market_hash_name": market_hash_name, "limit": 1}
    headers = {}
    if CSFLOAT_API_KEY:
        headers["Authorization"] = f"Bearer {CSFLOAT_API_KEY}"
    try:
        r = requests.get(CSFLOAT_LISTINGS_URL, params=params, headers=headers or None, timeout=10)
        if r.status_code == 429:
            return None
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, ValueError):
        return None
    listings = data if isinstance(data, list) else (data.get("listings") or data.get("data") or [])
    if not listings:
        return None
    first = listings[0] if isinstance(listings[0], dict) else {}
    price = first.get("price") or first.get("listing_price") or first.get("suggested_price")
    if price is None:
        return None
    return float(price) / 100.0 if price > 1000 else float(price)


def send_ntfy(topic: str, title: str, message: str, emoji: str = "🔔") -> bool:
    """POST to ntfy.sh. Returns True on success."""
    if not topic:
        return False
    url = f"https://ntfy.sh/{topic}"
    try:
        r = requests.post(
            url,
            data=message.encode("utf-8"),
            headers={
                "Title": f"{emoji} {title}",
                "Content-Type": "text/plain; charset=utf-8",
            },
            timeout=10,
        )
        return r.status_code in (200, 201, 204)
    except requests.RequestException:
        return False


def main():
    watchlist = get_watchlist()
    if not watchlist:
        print("Watchlist is empty. Add items in the dashboard (Manage watchlist) or set WATCHLIST in env.")
        time.sleep(60)
        return main()  # Retry after a minute
    if not NTFY_TOPIC:
        print("NTFY_TOPIC is empty. Alerts will be skipped.")

    price_cache: dict[str, float] = {}

    while True:
        watchlist = get_watchlist()  # Re-read in case user updated from UI
        if not watchlist:
            time.sleep(POLL_INTERVAL_SEC)
            continue

        for i, name in enumerate(watchlist):
            if i > 0:
                time.sleep(CSFLOAT_DELAY_SEC)
            price = fetch_csfloat_price(name)
            if price is None:
                if i > 0:
                    print("Rate limited or error; pausing loop.")
                    break
                continue
            prev = price_cache.get(name)
            price_cache[name] = price
            if prev is None:
                continue
            if prev <= 0:
                continue
            change_pct = (price - prev) / prev
            if abs(change_pct) < ALERT_THRESHOLD:
                continue
            emoji = "📈" if change_pct > 0 else "📉"
            pct_str = f"+{change_pct * 100:.1f}%" if change_pct > 0 else f"{change_pct * 100:.1f}%"
            title = f"CS2 price alert: {name[:50]}"
            msg = f"{emoji} {name}\n\n${prev:.2f} → ${price:.2f} ({pct_str})"
            if send_ntfy(NTFY_TOPIC, title, msg, emoji):
                print(f"Alert sent: {name} {pct_str}")
            else:
                print(f"Failed to send ntfy for: {name}")

        time.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    main()
