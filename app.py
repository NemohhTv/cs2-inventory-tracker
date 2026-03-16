"""
CS2 Inventory Price Tracker — Streamlit Dashboard
Dual pricing (Steam Market + CSFloat), price-change ticker, item images,
inventory browser, and settings from the UI.
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
PRICE_HISTORY_FILE = os.path.join(DATA_DIR, "price_history.json")

# ---------------------------------------------------------------------------
# API URLs
# ---------------------------------------------------------------------------
STEAM_INVENTORY_URL = "https://steamcommunity.com/inventory/{steam_id}/730/2"
STEAM_MARKET_PRICE_URL = "https://steamcommunity.com/market/priceoverview/"
STEAM_IMG_CDN = "https://community.akamai.steamstatic.com/economy/image/"
CSFLOAT_LISTINGS_URL = "https://csfloat.com/api/v1/listings"

# ---------------------------------------------------------------------------
# Rate-limit tunables
# ---------------------------------------------------------------------------
PRICE_DELAY_SEC = float(os.getenv("PRICE_DELAY_SEC", "3.0"))
CACHE_TTL_OVERRIDE = os.getenv("CSFLOAT_CACHE_TTL_SEC", "").strip()
MAX_ITEMS = int(os.getenv("CSFLOAT_MAX_ITEMS", "40"))
MIN_REFRESH_COOLDOWN = 60  # manual refresh cooldown in seconds


def _auto_cache_ttl(n_items: int) -> int:
    """Scale cache TTL based on watchlist size to balance speed vs rate limits."""
    if CACHE_TTL_OVERRIDE:
        return int(CACHE_TTL_OVERRIDE)
    if n_items <= 5:
        return 300     # 5 min
    if n_items <= 15:
        return 600     # 10 min
    if n_items <= 30:
        return 900     # 15 min
    return 1200        # 20 min

# =========================================================================
# Settings
# =========================================================================
def _ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def _read_json(path: str) -> dict:
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def _write_json(path: str, data: dict):
    _ensure_data_dir()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_settings() -> dict:
    return _read_json(SETTINGS_FILE)


def save_settings(s: dict):
    _write_json(SETTINGS_FILE, s)


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
                items = [l.strip() for l in f if l.strip()]
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
    cur = get_watchlist()
    if item.strip() and item.strip() not in cur:
        save_watchlist(cur + [item.strip()])


def remove_from_watchlist(item: str):
    save_watchlist([x for x in get_watchlist() if x != item])


# =========================================================================
# Image cache
# =========================================================================
def load_image_cache() -> dict[str, str]:
    return _read_json(IMAGE_CACHE_FILE)


def save_image_cache(cache: dict[str, str]):
    _write_json(IMAGE_CACHE_FILE, cache)


def get_item_image_url(name: str) -> str:
    icon = load_image_cache().get(name, "")
    return f"{STEAM_IMG_CDN}{icon}/360fx360f" if icon else ""


def market_url(name: str) -> str:
    """Steam Community Market listing URL for an item."""
    return f"https://steamcommunity.com/market/listings/730/{urllib.parse.quote(name)}"


def csfloat_url(name: str) -> str:
    """CSFloat search URL for an item."""
    return f"https://csfloat.com/search?market_hash_name={urllib.parse.quote(name)}"


# =========================================================================
# Price history (persisted between cache refreshes)
# =========================================================================
def load_price_history() -> dict:
    return _read_json(PRICE_HISTORY_FILE)


def save_price_history(h: dict):
    _write_json(PRICE_HISTORY_FILE, h)


# =========================================================================
# Steam inventory
# =========================================================================
def _fetch_steam_inventory_raw(steam_id: str) -> dict:
    if not steam_id:
        return {}
    try:
        r = requests.get(STEAM_INVENTORY_URL.format(steam_id=steam_id),
                         params={"l": "english", "count": 2000}, timeout=20)
        if r.status_code == 429:
            return {}
        r.raise_for_status()
        return r.json()
    except (requests.RequestException, ValueError):
        return {}


def _parse_inventory(inventory: dict) -> list[dict]:
    if not inventory:
        return []
    descriptions = inventory.get("descriptions") or []
    assets = inventory.get("assets") or []
    cid_info: dict[str, dict] = {}
    for d in descriptions:
        cid = d.get("classid", "")
        name = d.get("market_hash_name") or d.get("market_name") or ""
        icon = d.get("icon_url_large") or d.get("icon_url") or ""
        if cid and name:
            cid_info[cid] = {"name": name, "icon_url": icon}
    counts: dict[str, int] = {}
    for a in assets:
        info = cid_info.get(a.get("classid", ""))
        if info:
            counts[info["name"]] = counts.get(info["name"], 0) + 1
    ic = load_image_cache()
    out = []
    for name, qty in sorted(counts.items(), key=lambda x: x[0].lower()):
        icon = next((v["icon_url"] for v in cid_info.values() if v["name"] == name and v["icon_url"]), "")
        if icon:
            ic[name] = icon
        img = f"{STEAM_IMG_CDN}{icon}/360fx360f" if icon else ""
        out.append({"name": name, "qty": qty, "icon_url": icon, "image_url": img})
    save_image_cache(ic)
    return out


@st.cache_data(ttl=300, show_spinner="Loading inventory…")
def get_inventory_items(steam_id: str) -> list[dict]:
    if not steam_id:
        return []
    return _parse_inventory(_fetch_steam_inventory_raw(steam_id))


# =========================================================================
# Price fetchers
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


def _fetch_steam_market(name: str) -> float | None:
    try:
        r = requests.get(STEAM_MARKET_PRICE_URL,
                         params={"appid": "730", "currency": "1", "market_hash_name": name},
                         timeout=10)
        if r.status_code == 429:
            return None
        r.raise_for_status()
        d = r.json()
    except (requests.RequestException, ValueError):
        return None
    if not d.get("success"):
        return None
    return _parse_price_string(d.get("lowest_price", "")) or _parse_price_string(d.get("median_price", ""))


def _fetch_csfloat(name: str) -> tuple[float | None, bool]:
    """Returns (price, was_429)."""
    key = get_csfloat_key()
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    try:
        r = requests.get(CSFLOAT_LISTINGS_URL,
                         params={"market_hash_name": name, "limit": 1},
                         headers=headers or None, timeout=10)
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
    p = first.get("price") or first.get("listing_price") or first.get("suggested_price")
    if p is None:
        return None, False
    if isinstance(p, (int, float)):
        return (float(p) / 100.0 if p > 1000 else float(p)), False
    return None, False


# =========================================================================
# Combined data fetch (cached — TTL managed manually for dynamic scaling)
# =========================================================================
LAST_FETCH_FILE = os.path.join(DATA_DIR, "last_fetch_ts.json")


def _should_refetch(n_items: int) -> bool:
    """Check if enough time has passed since the last fetch."""
    ttl = _auto_cache_ttl(n_items)
    ts_data = _read_json(LAST_FETCH_FILE)
    last = ts_data.get("ts", 0)
    return (time.time() - last) >= ttl


def _mark_fetched():
    _write_json(LAST_FETCH_FILE, {"ts": time.time()})


@st.cache_data(ttl=300, show_spinner="Fetching prices…")
def fetch_watchlist_data(watchlist: tuple[str, ...], steam_id: str, _cache_bust: int = 0) -> tuple[list[dict], list[str]]:
    if not watchlist:
        return [], []

    inv = get_inventory_items(steam_id) if steam_id else []
    qty_map = {i["name"]: i["qty"] for i in inv}
    history = load_price_history()

    batch = list(watchlist[: max(1, MAX_ITEMS)])
    rows: list[dict] = []
    warnings: list[str] = []
    csfloat_hit_429 = False

    # Pass 1: Steam Market prices
    steam_prices: dict[str, float | None] = {}
    for idx, name in enumerate(batch):
        if idx > 0:
            time.sleep(max(1.0, PRICE_DELAY_SEC))
        steam_prices[name] = _fetch_steam_market(name)

    # Pass 2: CSFloat prices (always attempt, not just as fallback)
    cf_prices: dict[str, float | None] = {}
    for idx, name in enumerate(batch):
        if csfloat_hit_429:
            cf_prices[name] = None
            continue
        if idx > 0:
            time.sleep(max(1.0, PRICE_DELAY_SEC))
        p, was_429 = _fetch_csfloat(name)
        if was_429:
            csfloat_hit_429 = True
            warnings.append("CSFloat rate-limited — CSFloat prices may be partial.")
            cf_prices[name] = None
        else:
            cf_prices[name] = p

    # Build rows with deltas
    new_history: dict = {}
    for name in batch:
        sp = steam_prices.get(name)
        cp = cf_prices.get(name)
        prev = history.get(name, {})
        prev_steam = prev.get("steam")
        prev_cf = prev.get("csfloat")

        sp_r = round(sp, 2) if sp is not None else None
        cp_r = round(cp, 2) if cp is not None else None

        # Compute deltas
        steam_delta = round(sp_r - prev_steam, 2) if sp_r is not None and prev_steam is not None else None
        steam_pct = round((steam_delta / prev_steam) * 100, 2) if steam_delta is not None and prev_steam else None
        cf_delta = round(cp_r - prev_cf, 2) if cp_r is not None and prev_cf is not None else None
        cf_pct = round((cf_delta / prev_cf) * 100, 2) if cf_delta is not None and prev_cf else None

        # Primary price for totals: prefer Steam Market, fall back to CSFloat
        primary = sp_r if sp_r is not None else cp_r
        prev_primary = prev_steam if prev_steam is not None else prev_cf
        qty = qty_map.get(name, 0)
        total = round(primary * qty, 2) if primary is not None and qty > 0 else None
        prev_total = round(prev_primary * qty, 2) if prev_primary is not None and qty > 0 else None
        total_delta = round(total - prev_total, 2) if total is not None and prev_total is not None else None

        # Save to new history
        entry: dict = {}
        if sp_r is not None:
            entry["steam"] = sp_r
        elif prev_steam is not None:
            entry["steam"] = prev_steam  # keep old if we couldn't fetch
        if cp_r is not None:
            entry["csfloat"] = cp_r
        elif prev_cf is not None:
            entry["csfloat"] = prev_cf
        new_history[name] = entry

        rows.append({
            "name": name,
            "image_url": get_item_image_url(name),
            "qty": qty,
            "steam_price": sp_r,
            "steam_delta": steam_delta,
            "steam_pct": steam_pct,
            "cf_price": cp_r,
            "cf_delta": cf_delta,
            "cf_pct": cf_pct,
            "primary_price": primary,
            "total": total,
            "total_delta": total_delta,
        })

    # Persist history for next comparison
    for k, v in history.items():
        if k not in new_history:
            new_history[k] = v
    save_price_history(new_history)

    if len(batch) < len(watchlist):
        warnings.append(f"Showing {len(batch)} of {len(watchlist)} items (rate-limit cap).")

    _mark_fetched()
    return rows, warnings


# =========================================================================
# UI helpers
# =========================================================================
def _delta_html(delta: float | None, pct: float | None, prefix: str = "") -> str:
    """Return HTML for a price delta like '▲ +$5.38 (+3.5%)' in green/red."""
    if delta is None:
        return '<span style="color:#484f58;font-size:0.8rem;">—</span>'
    if delta > 0:
        arrow, color = "▲", "#22c55e"
        sign = "+"
    elif delta < 0:
        arrow, color = "▼", "#ef4444"
        sign = ""
    else:
        arrow, color = "—", "#8b949e"
        sign = ""
    pct_str = f" ({sign}{pct:.1f}%)" if pct is not None else ""
    return f'<span style="color:{color};font-size:0.85rem;font-weight:600;">{arrow} {sign}${abs(delta):,.2f}{pct_str}</span>'


def _price_block_html(label: str, price: float | None, delta: float | None, pct: float | None,
                      link: str = "") -> str:
    """Price box HTML for one source. Label links to marketplace if link is provided."""
    if price is not None:
        price_str = f'<span style="color:#58a6ff;font-size:1.25rem;font-weight:700;">${price:,.2f}</span>'
    else:
        price_str = '<span style="color:#484f58;font-size:1.1rem;">—</span>'
    delta_str = _delta_html(delta, pct)
    if link:
        label_html = (f'<a href="{link}" target="_blank" '
                      f'style="color:#8b949e;font-size:0.7rem;text-transform:uppercase;'
                      f'text-decoration:none;margin-bottom:2px;display:block;">{label} ↗</a>')
    else:
        label_html = f'<div style="color:#8b949e;font-size:0.7rem;text-transform:uppercase;margin-bottom:2px;">{label}</div>'
    return (
        f'<div style="background:#21262d;border-radius:8px;padding:0.6rem 0.75rem;flex:1;min-width:140px;">'
        f'{label_html}'
        f'{price_str}<br>{delta_str}'
        f'</div>'
    )


def _trading_card_html(r: dict) -> str:
    """Single trading-style item card: image, name link, primary price + % change, Steam|CSFloat, qty/value."""
    mkt = market_url(r["name"])
    cf = csfloat_url(r["name"])
    name_esc = (r["name"].replace("&", "&amp;").replace('"', "&quot;")
                .replace("<", "&lt;").replace(">", "&gt;"))
    img = r["image_url"]
    if not img:
        img_block = '<div class="card-img-placeholder">🔫</div>'
    else:
        img_block = f'<a href="{mkt}" target="_blank"><img src="{img}" class="card-img" alt=""/></a>'

    primary = r["primary_price"]
    steam_pct = r.get("steam_pct")
    cf_pct = r.get("cf_pct")
    pct = steam_pct if steam_pct is not None else cf_pct
    delta = r.get("steam_delta") if r.get("steam_delta") is not None else r.get("cf_delta")

    if primary is not None:
        price_str = f'<span class="card-price">${primary:,.2f}</span>'
    else:
        price_str = '<span class="card-price-muted">—</span>'

    if pct is not None:
        if pct > 0:
            chg = f'<span class="chg-up">▲ {pct:+.1f}%</span>'
        elif pct < 0:
            chg = f'<span class="chg-down">▼ {pct:.1f}%</span>'
        else:
            chg = '<span class="chg-flat">— 0.0%</span>'
    else:
        chg = '<span class="chg-flat">—</span>'

    steam_str = f'${r["steam_price"]:,.2f}' if r.get("steam_price") is not None else "—"
    cf_str = f'${r["cf_price"]:,.2f}' if r.get("cf_price") is not None else "—"
    sources = f'<a href="{mkt}" target="_blank" class="card-link">Steam</a> {steam_str} · <a href="{cf}" target="_blank" class="card-link">CSFloat</a> {cf_str}'

    qty = r["qty"]
    total = r["total"]
    qty_str = f"Qty: {qty}" if qty > 0 else "Not in inventory"
    val_str = f"${total:,.2f}" if total else "—"
    footer = f"{qty_str} · Value {val_str}"

    return f"""
    <div class="trading-card">
        <div class="card-img-wrap">{img_block}</div>
        <a href="{mkt}" target="_blank" class="card-name">{name_esc} ↗</a>
        <div class="card-price-row">{price_str} {chg}</div>
        <div class="card-sources">{sources}</div>
        <div class="card-footer">{footer}</div>
    </div>"""


CSS = """
<style>
    /* Base */
    .stApp { background: #0d1117; }
    .stApp > div { padding-top: 0.5rem; }
    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #161b22 0%, #0d1117 100%);
        border-right: 1px solid #21262d;
    }
    [data-testid="stSidebar"] .stMarkdown { color: #8b949e; }

    /* Header strip */
    .trading-header {
        background: #161b22;
        border-bottom: 1px solid #21262d;
        padding: 0.75rem 1.25rem;
        margin: -1rem -1rem 1rem -1rem;
        display: flex;
        align-items: center;
        justify-content: space-between;
        flex-wrap: wrap;
        gap: 0.5rem;
    }
    .trading-header h1 { margin: 0; font-size: 1.25rem; font-weight: 700; color: #e6edf3; }
    .trading-header .sub { color: #8b949e; font-size: 0.8rem; margin-top: 2px; }

    /* Portfolio ticker strip */
    .ticker-strip {
        display: flex;
        gap: 1.5rem;
        padding: 1rem 1.25rem;
        background: #161b22;
        border: 1px solid #21262d;
        border-radius: 10px;
        margin-bottom: 1.25rem;
        flex-wrap: wrap;
    }
    .ticker-item { display: flex; flex-direction: column; gap: 2px; }
    .ticker-label { color: #8b949e; font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.05em; }
    .ticker-value { color: #e6edf3; font-size: 1.35rem; font-weight: 700; font-variant-numeric: tabular-nums; }
    .ticker-delta-up { color: #3fb950; font-size: 0.9rem; font-weight: 600; }
    .ticker-delta-down { color: #f85149; font-size: 0.9rem; font-weight: 600; }
    .ticker-delta-flat { color: #8b949e; font-size: 0.9rem; }

    /* Trading cards grid */
    .cards-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 1rem; }
    .trading-card {
        background: #161b22;
        border: 1px solid #21262d;
        border-radius: 10px;
        padding: 1rem;
        transition: border-color 0.15s, box-shadow 0.15s;
    }
    .trading-card:hover { border-color: #30363d; box-shadow: 0 4px 12px rgba(0,0,0,0.3); }
    .card-img-wrap { text-align: center; margin-bottom: 0.75rem; }
    .card-img { width: 100%; max-width: 200px; height: auto; border-radius: 8px; }
    .card-img-placeholder {
        width: 100%; max-width: 200px; height: 120px; margin: 0 auto;
        background: #21262d; border-radius: 8px;
        display: flex; align-items: center; justify-content: center;
        color: #484f58; font-size: 2.5rem;
    }
    .card-name {
        display: block;
        color: #58a6ff;
        font-weight: 600;
        font-size: 0.9rem;
        text-decoration: none;
        margin-bottom: 0.5rem;
        line-height: 1.3;
        overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }
    .card-name:hover { text-decoration: underline; color: #79c0ff; }
    .card-price-row { display: flex; align-items: baseline; gap: 0.5rem; margin-bottom: 0.35rem; flex-wrap: wrap; }
    .card-price { color: #e6edf3; font-size: 1.5rem; font-weight: 700; font-variant-numeric: tabular-nums; }
    .card-price-muted { color: #484f58; font-size: 1.2rem; }
    .chg-up { color: #3fb950; font-size: 0.9rem; font-weight: 600; }
    .chg-down { color: #f85149; font-size: 0.9rem; font-weight: 600; }
    .chg-flat { color: #8b949e; font-size: 0.85rem; }
    .card-sources { color: #8b949e; font-size: 0.75rem; margin-bottom: 0.5rem; }
    .card-sources a { color: #58a6ff; text-decoration: none; }
    .card-sources a:hover { text-decoration: underline; }
    .card-footer { color: #6e7681; font-size: 0.8rem; }

    /* Tabs */
    [data-testid="stTabs"] > div:first-child { background: transparent; border-bottom: 1px solid #21262d; }
    [data-testid="stTabs"] [role="tab"] { color: #8b949e; }
    [data-testid="stTabs"] [aria-selected="true"] { color: #e6edf3; font-weight: 600; }

    /* Metrics */
    div[data-testid="stMetricValue"] { color: #e6edf3; font-variant-numeric: tabular-nums; }
    div[data-testid="stMetricLabel"] { color: #8b949e; }

    /* Inventory tab cards (reuse trading look) */
    .card { background: #161b22; border: 1px solid #21262d; border-radius: 10px; padding: 1rem; margin-bottom: 0.5rem; }
    .placeholder-img { width: 160px; height: 100px; background: #21262d; border-radius: 8px; display: flex; align-items: center; justify-content: center; color: #484f58; font-size: 2rem; }
    .item-title { font-weight: 600; font-size: 0.9rem; color: #e6edf3; }
</style>
"""


# =========================================================================
# Sidebar
# =========================================================================
def render_sidebar():
    with st.sidebar:
        st.header("Settings")
        s = load_settings()
        cur_sid = s.get("steam_id") or os.getenv("STEAM_ID", "")
        cur_cf = s.get("csfloat_api_key") or os.getenv("CSFLOAT_API_KEY", "")
        new_sid = st.text_input("Steam ID (64-bit)", value=cur_sid,
                                placeholder="76561198012345678",
                                help="Get yours at steamid.io")
        new_cf = st.text_input("CSFloat API key (optional)", value=cur_cf,
                               type="password",
                               help="Enables CSFloat pricing column")
        if st.button("Save settings", use_container_width=True):
            save_settings({"steam_id": new_sid.strip(), "csfloat_api_key": new_cf.strip()})
            st.cache_data.clear()
            st.success("Saved!")
            st.rerun()
        st.divider()
        st.caption(f"Steam ID: {'Set' if get_steam_id() else 'Not set'}")
        st.caption(f"CSFloat key: {'Set' if get_csfloat_key() else 'Not set'}")
        n = len(get_watchlist())
        st.caption(f"Auto-refresh: {_auto_cache_ttl(n) // 60} min ({n} items)  ·  Delay: {PRICE_DELAY_SEC}s")


# =========================================================================
# Main
# =========================================================================
def main():
    st.set_page_config(page_title="CS2 Inventory Tracker", page_icon="🎯",
                       layout="wide", initial_sidebar_state="auto")
    st.markdown(CSS, unsafe_allow_html=True)
    render_sidebar()

    steam_id = get_steam_id()
    watchlist = get_watchlist()
    watchlist_set = set(watchlist)

    # Header bar (trading-app style)
    st.markdown(
        '<div class="trading-header">'
        '<div><h1>CS2 Inventory Tracker</h1><div class="sub">Track prices · Steam Market & CSFloat · Portfolio value</div></div>'
        '</div>',
        unsafe_allow_html=True,
    )

    tab_dash, tab_inv, tab_manage = st.tabs(["Market", "Inventory", "Watchlist"])

    # ── Dashboard ─────────────────────────────────────────────
    with tab_dash:
        if not watchlist:
            st.info("Your watchlist is empty. Go to **My Inventory** to browse and add items, "
                    "or use **Manage Watchlist** to add them manually.")
        else:
            cache_ttl = _auto_cache_ttl(len(watchlist))

            # Use _cache_bust to force refetch when TTL expires or user clicks refresh
            ts_data = _read_json(LAST_FETCH_FILE)
            last_ts = ts_data.get("ts", 0)
            elapsed = time.time() - last_ts if last_ts else 999999
            cache_bust = int(last_ts) if elapsed < cache_ttl else int(time.time())

            # Refresh bar
            rc1, rc2, rc3 = st.columns([1, 2, 3])
            with rc1:
                if st.button("🔄 Refresh", use_container_width=True):
                    st.cache_data.clear()
                    st.rerun()
            with rc2:
                next_refresh = max(0, int(cache_ttl - elapsed))
                if next_refresh > 0:
                    st.caption(f"Next refresh in {next_refresh // 60}m {next_refresh % 60}s")
                else:
                    st.caption("Refreshing…")
            with rc3:
                st.caption(f"Last fetched: {time.strftime('%H:%M UTC', time.gmtime())} · Auto-refresh every {cache_ttl // 60} min")

            rows, warnings = fetch_watchlist_data(tuple(watchlist), steam_id, _cache_bust=cache_bust)
            for w in warnings:
                st.warning(w)

            if rows:
                # ── Portfolio ticker strip ──
                total_val = sum(r["total"] for r in rows if r["total"])
                total_prev = sum((r["total"] - r["total_delta"]) for r in rows if r["total"] and r["total_delta"] is not None)
                port_delta = round(total_val - total_prev, 2) if total_prev else None
                port_pct = round((port_delta / total_prev) * 100, 1) if port_delta and total_prev else None
                priced = sum(1 for r in rows if r["primary_price"] is not None)
                total_qty = sum(r["qty"] for r in rows)

                delta_class = "ticker-delta-up" if port_pct and port_pct > 0 else "ticker-delta-down" if port_pct and port_pct < 0 else "ticker-delta-flat"
                delta_text = f"+{port_delta:,.2f} (+{port_pct:.1f}%)" if port_delta is not None and port_pct is not None and port_delta >= 0 else f"{port_delta:,.2f} ({port_pct:.1f}%)" if port_delta is not None and port_pct is not None else "—"

                st.markdown(
                    f'<div class="ticker-strip">'
                    f'<div class="ticker-item"><span class="ticker-label">Portfolio value</span><span class="ticker-value">${total_val:,.2f}</span></div>'
                    f'<div class="ticker-item"><span class="ticker-label">Change</span><span class="{delta_class}">{delta_text}</span></div>'
                    f'<div class="ticker-item"><span class="ticker-label">Items</span><span class="ticker-value">{len(rows)}</span></div>'
                    f'<div class="ticker-item"><span class="ticker-label">Quantity</span><span class="ticker-value">{total_qty}</span></div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

                # ── Trading card grid ──
                n_cols = 3
                for i in range(0, len(rows), n_cols):
                    cols = st.columns(n_cols, gap="medium")
                    for j, col in enumerate(cols):
                        ri = i + j
                        if ri >= len(rows):
                            break
                        r = rows[ri]
                        with col:
                            st.markdown(_trading_card_html(r), unsafe_allow_html=True)
                            if st.button("Remove from watchlist", key=f"drm_{ri}", type="secondary", use_container_width=True):
                                remove_from_watchlist(r["name"])
                                st.cache_data.clear()
                                st.rerun()

                # ── Detail table (collapsible) ──
                with st.expander("View detail table (Steam / CSFloat / Qty)"):
                    table_rows = []
                    for r in rows:
                        sd = r["steam_delta"]
                        cd = r["cf_delta"]
                        table_rows.append({
                            "Item": r["name"],
                            "Market link": market_url(r["name"]),
                            "Steam (USD)": f"${r['steam_price']:,.2f}" if r["steam_price"] else "—",
                            "Steam Δ": f"{'+'if sd and sd>0 else ''}{sd:+,.2f}" if sd is not None else "—",
                            "CSFloat (USD)": f"${r['cf_price']:,.2f}" if r["cf_price"] else "—",
                            "CSFloat Δ": f"{'+'if cd and cd>0 else ''}{cd:+,.2f}" if cd is not None else "—",
                            "Qty": r["qty"],
                            "Total (USD)": f"${r['total']:,.2f}" if r["total"] else "—",
                        })
                    st.dataframe(
                        pd.DataFrame(table_rows),
                        use_container_width=True,
                        hide_index=True,
                        column_config={
                            "Market link": st.column_config.LinkColumn("Market link", display_text="Open ↗"),
                        },
                    )

            else:
                st.info("No price data yet. Prices appear after the first fetch cycle.")

    # ── My Inventory ──────────────────────────────────────────
    with tab_inv:
        if not steam_id:
            st.warning("Open the **sidebar** (arrow top-left) and enter your **Steam 64-bit ID** to load your inventory.")
        else:
            inv = get_inventory_items(steam_id)
            if not inv:
                st.warning("Could not load inventory. Make sure your **Steam profile** and **CS2 inventory** are set to **Public**.")
            else:
                st.caption(f"{len(inv)} unique items in your inventory")
                search = st.text_input("🔍 Search", placeholder="Filter by name…", key="inv_search")
                filtered = [i for i in inv if search.lower() in i["name"].lower()] if search else inv
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
                    st.markdown(f"**[{item}]({market_url(item)})**")
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
