"""
CS2 Inventory Price Tracker - Streamlit Dashboard
Fetches Steam inventory quantities and CSFloat prices for a watchlist.
"""
import os
import time
import requests
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

STEAM_ID = os.getenv("STEAM_ID", "").strip()
CSFLOAT_API_KEY = os.getenv("CSFLOAT_API_KEY", "").strip()
WATCHLIST_RAW = os.getenv("WATCHLIST", "")
WATCHLIST = [x.strip() for x in WATCHLIST_RAW.split(",") if x.strip()]

STEAM_INVENTORY_URL = "https://steamcommunity.com/inventory/{steam_id}/730/2"
CSFLOAT_LISTINGS_URL = "https://csfloat.com/api/v1/listings"
CSFLOAT_DELAY_SEC = 1.5


def fetch_steam_inventory(steam_id: str) -> dict:
    """Fetch CS2 inventory from Steam. Returns raw JSON or empty dict on failure."""
    if not steam_id:
        return {}
    url = STEAM_INVENTORY_URL.format(steam_id=steam_id)
    params = {"l": "english", "count": 2000}
    try:
        r = requests.get(url, params=params, timeout=15)
        if r.status_code == 429:
            return {}
        r.raise_for_status()
        return r.json()
    except requests.RequestException:
        return {}
    except ValueError:
        return {}


def count_inventory_by_market_name(inventory: dict) -> dict[str, int]:
    """From Steam inventory JSON, count how many of each market_hash_name we own."""
    counts: dict[str, int] = {}
    if not inventory:
        return counts
    descriptions = inventory.get("descriptions") or []
    assets = inventory.get("assets") or []
    # Build classid -> market_hash_name
    classid_to_name: dict[str, str] = {}
    for d in descriptions:
        classid = d.get("classid", "")
        name = d.get("market_hash_name") or d.get("market_name") or ""
        if classid and name:
            classid_to_name[classid] = name
    for a in assets:
        classid = a.get("classid", "")
        name = classid_to_name.get(classid)
        if name:
            counts[name] = counts.get(name, 0) + 1
    return counts


def fetch_csfloat_price(market_hash_name: str) -> float | None:
    """Fetch lowest listing price (USD) for one item from CSFloat. Returns None on error or no data."""
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
    # Support both list and dict response
    listings = data if isinstance(data, list) else (data.get("listings") or data.get("data") or [])
    if not listings:
        return None
    first = listings[0] if isinstance(listings[0], dict) else {}
    # Try common price fields (cents or dollars)
    price = first.get("price") or first.get("listing_price") or first.get("suggested_price")
    if price is None:
        return None
    # If in cents, convert to dollars
    if isinstance(price, (int, float)):
        return float(price) / 100.0 if price > 1000 else float(price)
    return None


@st.cache_data(ttl=900)
def fetch_watchlist_data() -> tuple[list[dict], str | None]:
    """
    Fetch inventory counts and CSFloat prices for all watchlist items.
    Returns (list of row dicts, error_message or None).
    """
    if not STEAM_ID or not WATCHLIST:
        return [], "Configure STEAM_ID and WATCHLIST in .env"

    inventory = fetch_steam_inventory(STEAM_ID)
    counts = count_inventory_by_market_name(inventory)
    if not inventory and WATCHLIST:
        # Could be private profile or Steam down
        pass  # We still try CSFloat for prices

    rows: list[dict] = []
    for i, name in enumerate(WATCHLIST):
        if i > 0:
            time.sleep(CSFLOAT_DELAY_SEC)
        price = fetch_csfloat_price(name)
        if price is None and i > 0:
            # Likely rate limited; stop to avoid more 429s
            break
        qty = counts.get(name, 0)
        total = (price * qty) if price is not None else None
        rows.append({
            "Item": name,
            "Qty": qty,
            "Unit Price (USD)": round(price, 2) if price is not None else None,
            "Total Value (USD)": round(total, 2) if total is not None else None,
        })

    return rows, None


def main():
    st.set_page_config(
        page_title="CS2 Inventory Tracker",
        page_icon="🎯",
        layout="wide",
        initial_sidebar_state="collapsed",
    )

    # Dark theme
    st.markdown("""
        <style>
        .stApp { background-color: #0e1117; }
        .metric-card { background: linear-gradient(135deg, #1a1d24 0%, #252830 100%);
                       padding: 1rem 1.25rem; border-radius: 10px; border: 1px solid #333;
                       margin-bottom: 0.5rem; }
        .metric-label { color: #8b949e; font-size: 0.85rem; }
        .metric-value { color: #58a6ff; font-size: 1.5rem; font-weight: 600; }
        .metric-sub { color: #7ee787; font-size: 0.9rem; }
        div[data-testid="stMetricValue"] { color: #58a6ff; }
        </style>
    """, unsafe_allow_html=True)

    st.title("🎯 CS2 Inventory Tracker")
    st.caption("Trader's cockpit — watchlist prices from CSFloat, quantities from Steam")

    rows, err = fetch_watchlist_data()
    last_updated = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    st.caption(f"Last updated: {last_updated} (cache 15 min)")

    if err:
        st.error(err)
        return

    if not rows:
        st.info("No watchlist data. Add items to WATCHLIST in .env and ensure STEAM_ID is set.")
        return

    # Stock-ticker style metric cards (3 per row)
    cols = st.columns(min(3, len(rows)) or 1)
    for idx, row in enumerate(rows):
        col = cols[idx % 3]
        with col:
            unit = row.get("Unit Price (USD)")
            total = row.get("Total Value (USD)")
            qty = row.get("Qty", 0)
            name = (row.get("Item") or "—")[:40] + ("…" if len(str(row.get("Item", ""))) > 40 else "")
            value_str = f"${unit:.2f}" if unit is not None else "—"
            delta_str = f"Qty: {qty} · Total: ${total:.2f}" if total is not None else f"Qty: {qty}"
            st.metric(label=name, value=value_str, delta=delta_str)

    st.divider()
    st.subheader("Watchlist table")
    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
