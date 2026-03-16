"""
CS2 Inventory Price Tracker - Streamlit Dashboard
Fetches Steam inventory quantities and CSFloat prices for a watchlist.
Watchlist can be edited in the UI; STEAM_ID and CSFLOAT_API_KEY are optional (add later).
"""
import os
import time
import requests
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

DATA_DIR = os.getenv("DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
WATCHLIST_FILE = os.path.join(DATA_DIR, "watchlist.txt")

STEAM_ID = os.getenv("STEAM_ID", "").strip()
CSFLOAT_API_KEY = os.getenv("CSFLOAT_API_KEY", "").strip()
WATCHLIST_RAW = os.getenv("WATCHLIST", "")
WATCHLIST_FROM_ENV = [x.strip() for x in WATCHLIST_RAW.split(",") if x.strip()]

STEAM_INVENTORY_URL = "https://steamcommunity.com/inventory/{steam_id}/730/2"
CSFLOAT_LISTINGS_URL = "https://csfloat.com/api/v1/listings"
CSFLOAT_DELAY_SEC = 1.5


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
    return WATCHLIST_FROM_ENV.copy()


def save_watchlist(items: list[str]) -> None:
    """Save watchlist to file (one item per line). Creates directory if needed."""
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(WATCHLIST_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(items) + ("\n" if items else ""))


def add_to_watchlist(item: str) -> None:
    """Add a single item to watchlist (no duplicate)."""
    current = get_watchlist()
    if item.strip() and item.strip() not in current:
        save_watchlist(current + [item.strip()])


def remove_from_watchlist(item: str) -> None:
    """Remove a single item from watchlist."""
    current = get_watchlist()
    save_watchlist([x for x in current if x != item])


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


@st.cache_data(ttl=300)
def get_inventory_items(steam_id: str) -> list[tuple[str, int]]:
    """Get list of (market_hash_name, qty) from Steam inventory, sorted by name."""
    if not steam_id:
        return []
    inv = fetch_steam_inventory(steam_id)
    counts = count_inventory_by_market_name(inv)
    return sorted(counts.items(), key=lambda x: x[0].lower())


def fetch_csfloat_price(market_hash_name: str, api_key: str = "") -> float | None:
    """Fetch lowest listing price (USD) for one item from CSFloat. Returns None on error or no data."""
    params = {"market_hash_name": market_hash_name, "limit": 1}
    headers = {}
    key = api_key or CSFLOAT_API_KEY
    if key:
        headers["Authorization"] = f"Bearer {key}"
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
    if isinstance(price, (int, float)):
        return float(price) / 100.0 if price > 1000 else float(price)
    return None


@st.cache_data(ttl=900)
def fetch_watchlist_data(watchlist: tuple[str, ...], steam_id: str) -> tuple[list[dict], str | None]:
    """
    Fetch inventory counts and CSFloat prices for all watchlist items.
    Returns (list of row dicts, error_message or None).
    """
    if not watchlist:
        return [], "Add items to your watchlist below (one market name per line)."

    inventory = fetch_steam_inventory(steam_id) if steam_id else {}
    counts = count_inventory_by_market_name(inventory)

    rows: list[dict] = []
    for i, name in enumerate(watchlist):
        if i > 0:
            time.sleep(CSFLOAT_DELAY_SEC)
        price = fetch_csfloat_price(name)
        if price is None and i > 0:
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

    st.markdown("""
        <style>
        .stApp { background-color: #0e1117; }
        div[data-testid="stMetricValue"] { color: #58a6ff; }
        </style>
    """, unsafe_allow_html=True)

    st.title("🎯 CS2 Inventory Tracker")
    st.caption("Trader's cockpit — watchlist prices from CSFloat, quantities from Steam")

    watchlist = get_watchlist()
    watchlist_set = set(watchlist)

    # --- My watchlist: current items + remove + add custom ---
    with st.expander("⭐ My watchlist", expanded=True):
        if watchlist:
            st.caption("Items you're tracking. Remove below or add more from your inventory.")
            for item in watchlist:
                c1, c2 = st.columns([5, 1])
                with c1:
                    st.text(item)
                with c2:
                    if st.button("Remove", key=f"rm_{item[:50]}", type="secondary"):
                        remove_from_watchlist(item)
                        st.cache_data.clear()
                        st.rerun()
            st.divider()
        else:
            st.caption("Add items from your inventory below, or add a custom item.")
        # Add custom item (not in inventory)
        custom = st.text_input("Add custom item (exact market name)", placeholder="e.g. AWP | Dragon Lore (Field-Tested)", key="custom_item")
        if st.button("Add to watchlist"):
            if custom and custom.strip():
                add_to_watchlist(custom.strip())
                st.cache_data.clear()
                st.success(f"Added: {custom.strip()}")
                st.rerun()
            else:
                st.warning("Enter an item name first.")

    # --- Your inventory: pick items to add to watchlist ---
    if STEAM_ID:
        inv_items = get_inventory_items(STEAM_ID)
        if inv_items:
            with st.expander("📦 Your inventory — add to watchlist", expanded=not watchlist):
                st.caption("Click **Add to watchlist** on items you want to track. Already watched items are marked.")
                # Show in a compact table with Add button per row (or multiselect)
                inv_names = [name for name, _ in inv_items]
                name_to_qty = dict(inv_items)
                to_add = st.multiselect(
                    "Select items from your inventory to add to watchlist",
                    options=inv_names,
                    format_func=lambda x: f"{x} (×{name_to_qty.get(x, 0)})",
                    key="inv_multiselect",
                )
                if st.button("Add selected to watchlist"):
                    for name in to_add:
                        add_to_watchlist(name)
                    if to_add:
                        st.cache_data.clear()
                        st.success(f"Added {len(to_add)} item(s) to watchlist.")
                        st.rerun()
                # Quick table of inventory (name, qty, on watchlist?)
                inv_df = pd.DataFrame([
                    {"Item": name, "Qty": qty, "On watchlist": "✓" if name in watchlist_set else "—"}
                    for name, qty in inv_items
                ])
                st.dataframe(inv_df, use_container_width=True, hide_index=True, height=min(300, 50 + len(inv_items) * 35))
        else:
            st.info("Could not load inventory. Check that your Steam profile and inventory are public.")
    else:
        st.caption("💡 Set **STEAM_ID** in your deployment environment to see your inventory and add items from it to your watchlist.")

    watchlist = get_watchlist()
    if not watchlist:
        st.info("Add at least one item from **My watchlist** or **Your inventory** above to see prices here.")
        return

    rows, err = fetch_watchlist_data(tuple(watchlist), STEAM_ID)
    last_updated = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    st.caption(f"Last updated: {last_updated} (cache 15 min)")

    if err:
        st.warning(err)
        return

    if not rows:
        st.info("No price data yet. Check item names or try again later.")
        return

    # Metric cards
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
