"""
CS2 Inventory Price Tracker — Streamlit Dashboard
Item images from Steam CDN, prices from Steam Market (primary) + CSFloat (optional),
quantities from Steam inventory. Watchlist and settings configurable from the UI.
"""
import json
import os
import re
import time
import urllib.parse

import pandas as pd
import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
DATA_DIR = os.getenv("DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
WATCHLIST_FILE = os.path.join(DATA_DIR, "watchlist.txt")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
IMAGE_CACHE_FILE = os.path.join(DATA_DIR, "image_cache.json")

# ---------------------------------------------------------------------------
# API URLs
# ---------------------------------------------------------------------------
STEAM_INVENTORY_URL = "https://steamcommunity.com/inventory/{steam_id}/730/2"
STEAM_MARKET_PRICE_URL = "https://steamcommunity.com/market/priceoverview/"
STEAM_IMG_CDN = "https://community.akamai.steamstatic.com/economy/image/"
CSFLOAT_LISTINGS_URL = "https://csfloat.com/api/v1/listings"

# ---------------------------------------------------------------------------
# Rate-limit tunables (env or defaults)
# ---------------------------------------------------------------------------
PRICE_DELAY_SEC = float(os.getenv("PRICE_DELAY_SEC", "3.0"))
CACHE_TTL_SEC = int(os.getenv("CSFLOAT_CACHE_TTL_SEC", "1200"))
MAX_ITEMS_PER_RUN = int(os.getenv("CSFLOAT_MAX_ITEMS", "40"))

# =========================================================================
# Settings (persisted to data/settings.json so user can configure from UI)
# =========================================================================
def _ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def load_settings() -> dict:
    if os.path.isfile(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def save_settings(settings: dict):
    _ensure_data_dir()
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)


def get_steam_id() -> str:
    return (load_settings().get("steam_id") or os.getenv("STEAM_ID", "")).strip()


def get_csfloat_key() -> str:
    return (load_settings().get("csfloat_api_key") or os.getenv("CSFLOAT_API_KEY", "")).strip()


# =========================================================================
# Watchlist
# =========================================================================
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


def save_watchlist(items: list[str]):
    _ensure_data_dir()
    with open(WATCHLIST_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(items) + ("\n" if items else ""))


def add_to_watchlist(item: str):
    current = get_watchlist()
    if item.strip() and item.strip() not in current:
        save_watchlist(current + [item.strip()])


def remove_from_watchlist(item: str):
    save_watchlist([x for x in get_watchlist() if x != item])


# =========================================================================
# Image cache (market_hash_name -> icon_url hash from Steam)
# =========================================================================
def load_image_cache() -> dict[str, str]:
    if os.path.isfile(IMAGE_CACHE_FILE):
        try:
            with open(IMAGE_CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def save_image_cache(cache: dict[str, str]):
    _ensure_data_dir()
    with open(IMAGE_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f)


def get_item_image_url(name: str) -> str:
    icon = load_image_cache().get(name, "")
    return f"{STEAM_IMG_CDN}{icon}/360fx360f" if icon else ""


# =========================================================================
# Steam inventory
# =========================================================================
def _fetch_steam_inventory_raw(steam_id: str) -> dict:
    if not steam_id:
        return {}
    url = STEAM_INVENTORY_URL.format(steam_id=steam_id)
    try:
        r = requests.get(url, params={"l": "english", "count": 2000}, timeout=20)
        if r.status_code == 429:
            return {}
        r.raise_for_status()
        return r.json()
    except (requests.RequestException, ValueError):
        return {}


def _parse_inventory(inventory: dict) -> list[dict]:
    """Returns sorted list of {name, qty, icon_url}."""
    if not inventory:
        return []
    descriptions = inventory.get("descriptions") or []
    assets = inventory.get("assets") or []

    classid_info: dict[str, dict] = {}
    for d in descriptions:
        cid = d.get("classid", "")
        name = d.get("market_hash_name") or d.get("market_name") or ""
        icon = d.get("icon_url_large") or d.get("icon_url") or ""
        if cid and name:
            classid_info[cid] = {"name": name, "icon_url": icon}

    counts: dict[str, int] = {}
    for a in assets:
        info = classid_info.get(a.get("classid", ""))
        if info:
            counts[info["name"]] = counts.get(info["name"], 0) + 1

    image_cache = load_image_cache()
    results = []
    for name, qty in sorted(counts.items(), key=lambda x: x[0].lower()):
        icon = next((v["icon_url"] for v in classid_info.values() if v["name"] == name and v["icon_url"]), "")
        if icon:
            image_cache[name] = icon
        img = f"{STEAM_IMG_CDN}{icon}/360fx360f" if icon else ""
        results.append({"name": name, "qty": qty, "icon_url": icon, "image_url": img})
    save_image_cache(image_cache)
    return results


@st.cache_data(ttl=300, show_spinner="Loading inventory…")
def get_inventory_items(steam_id: str) -> list[dict]:
    if not steam_id:
        return []
    return _parse_inventory(_fetch_steam_inventory_raw(steam_id))


# =========================================================================
# Prices
# =========================================================================
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


def fetch_steam_market_price(name: str) -> dict | None:
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
    return {
        "lowest": _parse_price_string(data.get("lowest_price", "")),
        "median": _parse_price_string(data.get("median_price", "")),
        "volume": data.get("volume", "0").replace(",", ""),
    }


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
    if isinstance(price, (int, float)):
        return (float(price) / 100.0 if price > 1000 else float(price)), False
    return None, False


@st.cache_data(ttl=CACHE_TTL_SEC, show_spinner="Fetching prices…")
def fetch_watchlist_data(watchlist: tuple[str, ...], steam_id: str) -> tuple[list[dict], list[str]]:
    if not watchlist:
        return [], []

    inv = get_inventory_items(steam_id) if steam_id else []
    qty_map = {i["name"]: i["qty"] for i in inv}

    batch = list(watchlist[: max(1, MAX_ITEMS_PER_RUN)])
    rows: list[dict] = []
    warnings: list[str] = []

    for idx, name in enumerate(batch):
        if idx > 0:
            time.sleep(max(1.0, PRICE_DELAY_SEC))

        price = None
        source = ""

        # Primary: Steam Market
        mkt = fetch_steam_market_price(name)
        if mkt:
            price = mkt.get("lowest") or mkt.get("median")
            if price is not None:
                source = "Steam Market"

        # Fallback: CSFloat (only if we have a key)
        if price is None and get_csfloat_key():
            time.sleep(max(1.0, PRICE_DELAY_SEC))
            cf, was_429 = fetch_csfloat_price(name)
            if was_429:
                warnings.append("CSFloat rate-limited; partial data shown.")
                break
            if cf is not None:
                price, source = cf, "CSFloat"

        qty = qty_map.get(name, 0)
        total = round(price * qty, 2) if price is not None and qty > 0 else None

        rows.append({
            "name": name,
            "price": round(price, 2) if price is not None else None,
            "qty": qty,
            "total": total,
            "source": source,
            "image_url": get_item_image_url(name),
        })

    if len(batch) < len(watchlist):
        warnings.append(f"Showing {len(batch)} of {len(watchlist)} items (rate-limit cap).")

    return rows, warnings


# =========================================================================
# UI
# =========================================================================
CSS = """
<style>
    .stApp { background-color: #0e1117; }
    [data-testid="stSidebar"] { background-color: #161b22; }
    div[data-testid="stMetricValue"] { color: #58a6ff; }
    .card {
        background: linear-gradient(135deg, #161b22 0%, #1c2128 100%);
        border: 1px solid #30363d;
        border-radius: 12px;
        padding: 1rem;
        margin-bottom: 0.5rem;
        transition: border-color 0.2s;
    }
    .card:hover { border-color: #58a6ff; }
    .price-tag { color: #58a6ff; font-size: 1.35rem; font-weight: 700; }
    .price-source { color: #484f58; font-size: 0.72rem; margin-left: 4px; }
    .item-title { font-weight: 600; font-size: 0.95rem; color: #e6edf3;
                  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .meta { color: #8b949e; font-size: 0.82rem; }
    .placeholder-img { width: 120px; height: 90px; background: #21262d;
                       border-radius: 8px; display: flex; align-items: center;
                       justify-content: center; color: #484f58; font-size: 2.5rem; }
</style>
"""


def render_sidebar():
    with st.sidebar:
        st.header("Settings")
        settings = load_settings()
        cur_sid = settings.get("steam_id") or os.getenv("STEAM_ID", "")
        cur_cf = settings.get("csfloat_api_key") or os.getenv("CSFLOAT_API_KEY", "")

        new_sid = st.text_input("Steam ID (64-bit)", value=cur_sid,
                                placeholder="76561198012345678",
                                help="Get yours at steamid.io")
        new_cf = st.text_input("CSFloat API key (optional)", value=cur_cf,
                               type="password",
                               help="Adds CSFloat as a secondary price source")

        if st.button("Save settings", use_container_width=True):
            save_settings({"steam_id": new_sid.strip(), "csfloat_api_key": new_cf.strip()})
            st.cache_data.clear()
            st.success("Saved! Reloading…")
            st.rerun()

        st.divider()
        st.caption(f"Steam ID: {'Set' if get_steam_id() else 'Not set'}")
        st.caption(f"CSFloat key: {'Set' if get_csfloat_key() else 'Not set – using Steam Market'}")
        st.caption(f"Price cache: {CACHE_TTL_SEC // 60} min")


def main():
    st.set_page_config(page_title="CS2 Inventory Tracker", page_icon="🎯",
                       layout="wide", initial_sidebar_state="auto")
    st.markdown(CSS, unsafe_allow_html=True)
    render_sidebar()

    st.title("🎯 CS2 Inventory Tracker")
    st.caption("Track prices, quantities, and portfolio value for your CS2 skins")

    steam_id = get_steam_id()
    watchlist = get_watchlist()
    watchlist_set = set(watchlist)

    tab_dash, tab_inv, tab_manage = st.tabs(["📊 Dashboard", "📦 My Inventory", "⭐ Manage Watchlist"])

    # ── Dashboard ─────────────────────────────────────────────
    with tab_dash:
        if not watchlist:
            st.info("Your watchlist is empty. Go to **My Inventory** to browse and add items, "
                    "or use **Manage Watchlist** to add them manually.")
        else:
            rows, warnings = fetch_watchlist_data(tuple(watchlist), steam_id)
            ts = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
            st.caption(f"Last refreshed: {ts}  ·  Cache: {CACHE_TTL_SEC // 60} min")
            for w in warnings:
                st.warning(w)

            if rows:
                # Portfolio summary
                total_val = sum(r["total"] for r in rows if r["total"])
                priced = sum(1 for r in rows if r["price"] is not None)
                total_qty = sum(r["qty"] for r in rows)
                c1, c2, c3 = st.columns(3)
                c1.metric("Portfolio value", f"${total_val:,.2f}" if total_val else "—")
                c2.metric("Items tracked", f"{len(rows)} ({priced} priced)")
                c3.metric("Total quantity", str(total_qty))
                st.divider()

                # Item cards – 2 per row
                for i in range(0, len(rows), 2):
                    cols = st.columns(2, gap="medium")
                    for j, col in enumerate(cols):
                        ri = i + j
                        if ri >= len(rows):
                            break
                        r = rows[ri]
                        with col:
                            st.markdown('<div class="card">', unsafe_allow_html=True)
                            ic, dc = st.columns([1, 3])
                            with ic:
                                if r["image_url"]:
                                    st.image(r["image_url"], width=130)
                                else:
                                    st.markdown('<div class="placeholder-img">🔫</div>',
                                                unsafe_allow_html=True)
                            with dc:
                                st.markdown(f'<div class="item-title">{r["name"]}</div>',
                                            unsafe_allow_html=True)
                                if r["price"] is not None:
                                    st.markdown(
                                        f'<span class="price-tag">${r["price"]:,.2f}</span>'
                                        f'<span class="price-source">via {r["source"]}</span>',
                                        unsafe_allow_html=True)
                                else:
                                    st.markdown('<span class="meta">Price unavailable</span>',
                                                unsafe_allow_html=True)
                                parts = []
                                if r["qty"] > 0:
                                    parts.append(f"Qty: **{r['qty']}**")
                                else:
                                    parts.append("Not in inventory")
                                if r["total"]:
                                    parts.append(f"Total: **${r['total']:,.2f}**")
                                st.caption("  ·  ".join(parts))
                            bc1, bc2 = st.columns([4, 1])
                            with bc2:
                                if st.button("Remove", key=f"drm_{ri}", type="secondary"):
                                    remove_from_watchlist(r["name"])
                                    st.cache_data.clear()
                                    st.rerun()
                            st.markdown('</div>', unsafe_allow_html=True)

                # Table view
                st.divider()
                st.subheader("Detail table")
                df = pd.DataFrame([{
                    "Item": r["name"],
                    "Price (USD)": f"${r['price']:,.2f}" if r["price"] else "—",
                    "Qty": r["qty"],
                    "Total (USD)": f"${r['total']:,.2f}" if r["total"] else "—",
                    "Source": r["source"] or "—",
                } for r in rows])
                st.dataframe(df, use_container_width=True, hide_index=True)
            else:
                st.info("No price data yet. Prices appear after the first fetch cycle.")

    # ── My Inventory ──────────────────────────────────────────
    with tab_inv:
        if not steam_id:
            st.warning("Open the **sidebar** (arrow top-left) and enter your **Steam 64-bit ID** to load your inventory.")
        else:
            inv = get_inventory_items(steam_id)
            if not inv:
                st.warning("Could not load your inventory. Make sure your **Steam profile** and **CS2 inventory** are set to **Public**.")
            else:
                st.caption(f"{len(inv)} unique items in your inventory")
                search = st.text_input("🔍 Search", placeholder="Filter by name…", key="inv_search")
                filtered = inv
                if search:
                    q = search.lower()
                    filtered = [i for i in inv if q in i["name"].lower()]
                st.caption(f"Showing {len(filtered)} items")

                for i in range(0, len(filtered), 3):
                    cols = st.columns(3, gap="medium")
                    for j, col in enumerate(cols):
                        idx = i + j
                        if idx >= len(filtered):
                            break
                        it = filtered[idx]
                        watched = it["name"] in watchlist_set
                        with col:
                            st.markdown('<div class="card">', unsafe_allow_html=True)
                            if it["image_url"]:
                                st.image(it["image_url"], width=160)
                            else:
                                st.markdown('<div class="placeholder-img">🔫</div>',
                                            unsafe_allow_html=True)
                            st.markdown(f'<div class="item-title">{it["name"]}</div>',
                                        unsafe_allow_html=True)
                            st.caption(f"Qty: {it['qty']}")
                            if watched:
                                st.success("⭐ On watchlist", icon="⭐")
                            else:
                                if st.button("⭐ Add to watchlist", key=f"iadd_{idx}",
                                             use_container_width=True):
                                    add_to_watchlist(it["name"])
                                    st.cache_data.clear()
                                    st.rerun()
                            st.markdown('</div>', unsafe_allow_html=True)

    # ── Manage Watchlist ──────────────────────────────────────
    with tab_manage:
        st.subheader("Current watchlist")
        if watchlist:
            for wi, item in enumerate(watchlist):
                img = get_item_image_url(item)
                c1, c2, c3 = st.columns([1, 5, 1])
                with c1:
                    if img:
                        st.image(img, width=60)
                    else:
                        st.markdown("🔫")
                with c2:
                    st.markdown(f"**{item}**")
                with c3:
                    if st.button("Remove", key=f"mrm_{wi}", type="secondary"):
                        remove_from_watchlist(item)
                        st.cache_data.clear()
                        st.rerun()
        else:
            st.info("Watchlist is empty. Add items from **My Inventory** or type a name below.")

        st.divider()
        st.subheader("Add custom item")
        st.caption("Enter the exact market hash name (copy from Steam Market URL or CSFloat).")
        custom = st.text_input("Market hash name",
                               placeholder="e.g. AWP | Dragon Lore (Field-Tested)",
                               key="custom_add")
        if st.button("Add to watchlist", key="custom_btn", use_container_width=True):
            if custom and custom.strip():
                add_to_watchlist(custom.strip())
                st.cache_data.clear()
                st.success(f"Added: {custom.strip()}")
                st.rerun()
            else:
                st.warning("Enter an item name first.")


if __name__ == "__main__":
    main()
