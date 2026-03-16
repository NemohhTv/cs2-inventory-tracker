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
    """Returns (price_usd, was_429). Price from the cheapest listed item."""
    key = get_csfloat_key()
    if not key:
        return None, False
    headers = {"Authorization": key}
    try:
        r = requests.get(CSFLOAT_LISTINGS_URL,
                         params={"market_hash_name": name, "limit": 1,
                                 "sort_by": "lowest_price", "type": "buy_now"},
                         headers=headers, timeout=10)
        if r.status_code == 429:
            return None, True
        if r.status_code == 401:
            return None, False
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, ValueError):
        return None, False
    listings = data if isinstance(data, list) else (data.get("data") or [])
    if not listings:
        return None, False
    first = listings[0] if isinstance(listings[0], dict) else {}
    p = first.get("price")
    if p is None or not isinstance(p, (int, float)):
        return None, False
    return round(float(p) / 100.0, 2), False


# =========================================================================
# Combined data fetch (persistent file cache so redeploys don't spam APIs)
# =========================================================================
LAST_FETCH_FILE = os.path.join(DATA_DIR, "last_fetch_ts.json")
LAST_FETCH_CACHE_FILE = os.path.join(DATA_DIR, "last_fetch_cache.json")


def _mark_fetched():
    _write_json(LAST_FETCH_FILE, {"ts": time.time()})


def _invalidate_fetch_cache():
    """Delete the persistent file cache so the next load does a fresh API fetch."""
    for p in (LAST_FETCH_CACHE_FILE, LAST_FETCH_FILE):
        try:
            if os.path.isfile(p):
                os.remove(p)
        except OSError:
            pass


def _load_fetch_cache() -> dict | None:
    data = _read_json(LAST_FETCH_CACHE_FILE)
    if not data or not data.get("rows"):
        return None
    return data


def _save_fetch_cache(ts: float, watchlist: list, steam_id: str, rows: list, warnings: list):
    # Store only JSON-serializable row fields (no need for image_url; we recompute on display)
    out = {
        "ts": ts,
        "watchlist": watchlist,
        "steam_id": steam_id,
        "rows": rows,
        "warnings": warnings,
    }
    _write_json(LAST_FETCH_CACHE_FILE, out)


@st.cache_data(ttl=300, show_spinner="Fetching prices…")
def fetch_watchlist_data(watchlist: tuple[str, ...], steam_id: str, _cache_bust: int = 0) -> tuple[list[dict], list[str]]:
    if not watchlist:
        return [], []

    cache_ttl = _auto_cache_ttl(len(watchlist))
    watchlist_list = list(watchlist)

    # Use persistent file cache so redeploy doesn't trigger API calls
    cached = _load_fetch_cache()
    if cached:
        if (cached.get("watchlist") == watchlist_list and cached.get("steam_id") == steam_id
                and (time.time() - cached.get("ts", 0)) < cache_ttl):
            return cached.get("rows", []), cached.get("warnings", [])

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

    # Pass 2: CSFloat prices (only if API key is configured)
    cf_prices: dict[str, float | None] = {}
    has_cf_key = bool(get_csfloat_key())
    if has_cf_key:
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
    else:
        for name in batch:
            cf_prices[name] = None

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

    ts = time.time()
    _mark_fetched()
    _save_fetch_cache(ts, watchlist_list, steam_id, rows, warnings)
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


def _portfolio_change_html(port_delta: float | None, port_pct: float | None) -> str:
    """Portfolio-level change badge: same style as item cards (up/down/same/none)."""
    if port_pct is not None:
        if port_pct > 0:
            d_str = f"+${port_delta:,.2f}" if port_delta else ""
            delta_span = f' <span class="chg-delta">{d_str}</span>' if d_str else ""
            return f'<span class="chg-badge chg-up"><span class="chg-arrow">↑</span><span class="chg-pct">+{port_pct:.1f}%</span>{delta_span}</span>'
        elif port_pct < 0:
            d_str = f"−${abs(port_delta):,.2f}" if port_delta else ""
            delta_span = f' <span class="chg-delta">{d_str}</span>' if d_str else ""
            return f'<span class="chg-badge chg-down"><span class="chg-arrow">↓</span><span class="chg-pct">{port_pct:.1f}%</span>{delta_span}</span>'
        else:
            return '<span class="chg-badge chg-same"><span class="chg-arrow">●</span><span class="chg-pct">0.0%</span></span>'
    return '<span class="chg-badge chg-none"><span class="chg-arrow">—</span><span class="chg-pct">No data</span></span>'


_STEAM_ICON = (
    '<svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor">'
    '<path d="M12 2a10 10 0 0 0-9.96 9.04l5.35 2.21a2.83 2.83 0 0 1 1.6-.49c.05 0 '
    '.1 0 .16.002l2.39-3.46v-.05a3.79 3.79 0 1 1 3.79 3.79h-.09l-3.4 2.43c0 .06.01'
    '.12.01.18a2.84 2.84 0 0 1-5.65.36L2.4 14.47A10 10 0 1 0 12 2z"/></svg>'
)
_CSFLOAT_ICON = (
    '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" '
    'stroke-width="2.2" stroke-linecap="round">'
    '<path d="M13 4c-1.5 0-3.5.8-3.5 3.5S11 10 11 12s-1.5 4.5-1.5 4.5"/>'
    '<line x1="8" y1="10" x2="13" y2="10"/></svg>'
)


def _mini_pct_html(pct: float | None) -> str:
    """Small green/red percentage text for inside source pills."""
    if pct is None:
        return ""
    if pct > 0:
        return f'<span class="src-chg src-chg-up">+{pct:.1f}%</span>'
    elif pct < 0:
        return f'<span class="src-chg src-chg-down">{pct:.1f}%</span>'
    return f'<span class="src-chg src-chg-flat">0.0%</span>'


def _trading_card_html(r: dict) -> str:
    """Item card with image, dual pricing (Steam + CSFloat icons), change badge."""
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
            d_str = f"+${abs(delta):,.2f}" if delta is not None and delta != 0 else ""
            delta_span = f' <span class="chg-delta">{d_str}</span>' if d_str else ""
            chg = f'<span class="chg-badge chg-up"><span class="chg-arrow">↑</span><span class="chg-pct">+{pct:.1f}%</span>{delta_span}</span>'
        elif pct < 0:
            d_str = f"−${abs(delta):,.2f}" if delta is not None and delta != 0 else ""
            delta_span = f' <span class="chg-delta">{d_str}</span>' if d_str else ""
            chg = f'<span class="chg-badge chg-down"><span class="chg-arrow">↓</span><span class="chg-pct">{pct:.1f}%</span>{delta_span}</span>'
        else:
            chg = '<span class="chg-badge chg-same"><span class="chg-arrow">●</span><span class="chg-pct">0.0%</span></span>'
    else:
        chg = '<span class="chg-badge chg-none"><span class="chg-arrow">—</span><span class="chg-pct">No data</span></span>'

    sp = r.get("steam_price")
    cp = r.get("cf_price")
    steam_val = f"${sp:,.2f}" if sp is not None else "—"
    cf_val = f"${cp:,.2f}" if cp is not None else "—"

    s_pct = r.get("steam_pct")
    c_pct = r.get("cf_pct")
    steam_chg = _mini_pct_html(s_pct)
    cf_chg = _mini_pct_html(c_pct)

    qty = r["qty"]
    total = r["total"]
    qty_val = str(qty) if qty > 0 else "0"
    total_val = f"${total:,.2f}" if total else "—"

    return f"""
    <div class="trading-card">
        <div class="card-img-wrap">{img_block}</div>
        <a href="{mkt}" target="_blank" class="card-name">{name_esc}</a>
        <div class="card-price-row">{price_str} {chg}</div>
        <div class="card-bottom">
            <div class="card-sources">
                <a href="{mkt}" target="_blank" class="src-pill src-steam" title="Steam Market">
                    {_STEAM_ICON}<span class="src-price">{steam_val}</span>{steam_chg}
                </a>
                <a href="{cf}" target="_blank" class="src-pill src-csfloat" title="CSFloat">
                    {_CSFLOAT_ICON}<span class="src-price">{cf_val}</span>{cf_chg}
                </a>
            </div>
            <div class="card-meta">
                <span class="meta-item">Qty <strong>{qty_val}</strong></span>
                <span class="meta-sep"></span>
                <span class="meta-item">Value <strong>{total_val}</strong></span>
            </div>
        </div>
    </div>"""


CSS = """
<style>
    /* ── Reset Streamlit chrome ──────────────────────────── */
    [data-testid="stToolbar"] { display: none !important; }
    [data-testid="stHeader"]  { background: transparent !important; }
    header[data-testid="stHeader"] { pointer-events: none; }
    .stApp { background: #0d1117; }
    .block-container {
        padding-top: 0.5rem !important;
        padding-bottom: 2rem !important;
        max-width: 100% !important;
    }

    /* ── Tabs ────────────────────────────────────────────── */
    [data-testid="stTabs"] > div:first-child {
        background: transparent;
        border-bottom: 1px solid #21262d;
    }
    [data-testid="stTabs"] [role="tab"] {
        color: #8b949e;
        font-size: 0.85rem;
        padding: 0.5rem 1rem;
    }
    [data-testid="stTabs"] [aria-selected="true"] {
        color: #e6edf3;
        font-weight: 600;
        border-bottom-color: #58a6ff !important;
    }

    /* ── Stats bar ──────────────────────────────────────── */
    .stats-bar {
        display: flex;
        align-items: center;
        justify-content: space-between;
        background: linear-gradient(135deg, #161b22 0%, #1c2129 100%);
        border: 1px solid #21262d;
        border-radius: 10px;
        padding: 0.75rem 1.25rem;
        margin-bottom: 0.75rem;
        gap: 1rem;
    }
    .stats-bar-left { display: flex; gap: 2rem; align-items: center; flex-wrap: wrap; }
    .stats-bar-right { text-align: right; white-space: nowrap; }
    .stats-bar-meta { color: #484f58; font-size: 0.7rem; }
    .ticker-item {
        display: inline-flex;
        flex-direction: column;
        gap: 2px;
        min-width: 4rem;
    }
    .ticker-item-change { min-width: 8rem; }
    .ticker-sep {
        width: 1px;
        height: 1.8rem;
        background: #30363d;
        flex-shrink: 0;
    }
    .ticker-change-wrap { display: inline-flex; align-items: center; }
    .ticker-label {
        color: #484f58;
        font-size: 0.6rem;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        line-height: 1;
    }
    .ticker-value {
        color: #e6edf3;
        font-size: 1.15rem;
        font-weight: 700;
        font-variant-numeric: tabular-nums;
        line-height: 1.4;
    }

    /* ── Trading cards ──────────────────────────────────── */
    .trading-card {
        background: #161b22;
        border: 1px solid #21262d;
        border-radius: 10px;
        padding: 0.85rem;
        transition: border-color 0.2s, box-shadow 0.2s, transform 0.2s;
    }
    .trading-card:hover {
        border-color: #30363d;
        box-shadow: 0 4px 16px rgba(0,0,0,0.3);
        transform: translateY(-2px);
    }
    .card-img-wrap { text-align: center; margin-bottom: 0.5rem; }
    .card-img {
        width: 100%; max-width: 150px; height: auto;
        border-radius: 8px;
        filter: drop-shadow(0 2px 6px rgba(0,0,0,0.3));
    }
    .card-img-placeholder {
        width: 100%; max-width: 150px; height: 90px; margin: 0 auto;
        background: #21262d; border-radius: 8px;
        display: flex; align-items: center; justify-content: center;
        color: #484f58; font-size: 1.75rem;
    }
    .card-name {
        display: block;
        color: #58a6ff;
        font-weight: 600;
        font-size: 0.82rem;
        text-decoration: none;
        margin-bottom: 0.4rem;
        line-height: 1.3;
        overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }
    .card-name:hover { text-decoration: underline; color: #79c0ff; }
    .card-price-row {
        display: flex;
        align-items: center;
        gap: 0.5rem;
        margin-bottom: 0.35rem;
        flex-wrap: wrap;
    }
    .card-price {
        color: #e6edf3;
        font-size: 1.25rem;
        font-weight: 700;
        font-variant-numeric: tabular-nums;
    }
    .card-price-muted { color: #484f58; font-size: 1rem; }

    /* Change badges */
    .chg-badge {
        display: inline-flex;
        align-items: center;
        gap: 0.25rem;
        font-variant-numeric: tabular-nums;
        padding: 0.2rem 0.45rem;
        border-radius: 6px;
        font-weight: 600;
    }
    .chg-arrow  { font-size: 1.1rem; line-height: 1; }
    .chg-pct    { font-size: 0.85rem; }
    .chg-delta  { font-size: 0.75rem; opacity: 0.9; }
    .chg-badge.chg-up   { color: #3fb950; background: rgba(63, 185, 80, 0.15); }
    .chg-badge.chg-down { color: #f85149; background: rgba(248, 81, 73, 0.12); }
    .chg-badge.chg-same { color: #d4a72c; background: rgba(212, 167, 44, 0.12); }
    .chg-badge.chg-same .chg-arrow { font-size: 0.65rem; }
    .chg-badge.chg-none { color: #6e7681; background: rgba(110, 118, 129, 0.1); font-weight: 500; }
    .chg-badge.chg-none .chg-pct { font-size: 0.75rem; }

    /* Card bottom section */
    .card-bottom {
        border-top: 1px solid #21262d;
        margin-top: 0.4rem;
        padding-top: 0.5rem;
    }
    .card-sources {
        display: flex;
        align-items: center;
        gap: 0.4rem;
        margin-bottom: 0.4rem;
    }
    .src-pill {
        display: inline-flex;
        align-items: center;
        gap: 0.35rem;
        text-decoration: none;
        font-variant-numeric: tabular-nums;
        padding: 0.3rem 0.6rem;
        border-radius: 8px;
        transition: background 0.15s, transform 0.15s;
        flex: 1;
        justify-content: center;
    }
    .src-pill:hover { text-decoration: none; transform: translateY(-1px); }
    .src-pill svg { flex-shrink: 0; opacity: 0.85; }
    .src-price { font-size: 0.8rem; font-weight: 700; }
    .src-steam {
        color: #66c0f4;
        background: rgba(102, 192, 244, 0.1);
        border: 1px solid rgba(102, 192, 244, 0.15);
    }
    .src-steam:hover { background: rgba(102, 192, 244, 0.2); }
    .src-csfloat {
        color: #a78bfa;
        background: rgba(167, 139, 250, 0.1);
        border: 1px solid rgba(167, 139, 250, 0.15);
    }
    .src-csfloat:hover { background: rgba(167, 139, 250, 0.2); }

    .src-chg {
        font-size: 0.68rem;
        font-weight: 600;
        margin-left: auto;
        font-variant-numeric: tabular-nums;
    }
    .src-chg-up   { color: #3fb950; }
    .src-chg-down { color: #f85149; }
    .src-chg-flat { color: #6e7681; }

    .card-meta {
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 0.5rem;
        color: #6e7681;
        font-size: 0.72rem;
    }
    .card-meta strong {
        color: #8b949e;
        font-weight: 600;
    }
    .meta-sep {
        width: 3px; height: 3px;
        background: #30363d;
        border-radius: 50%;
        flex-shrink: 0;
    }

    /* ── Metrics ─────────────────────────────────────────── */
    div[data-testid="stMetricValue"] { color: #e6edf3; font-variant-numeric: tabular-nums; }
    div[data-testid="stMetricLabel"] { color: #8b949e; }

    /* ── Inventory tab ───────────────────────────────────── */
    .card {
        background: #161b22;
        border: 1px solid #21262d;
        border-radius: 10px;
        padding: 1rem;
        margin-bottom: 0.5rem;
    }
    .placeholder-img {
        width: 160px; height: 100px;
        background: #21262d; border-radius: 8px;
        display: flex; align-items: center; justify-content: center;
        color: #484f58; font-size: 2rem;
    }
    .item-title { font-weight: 600; font-size: 0.9rem; color: #e6edf3; }

    /* ── Settings tab ────────────────────────────────────── */
    .stTextInput input {
        background: #0d1117 !important;
        border-color: #30363d !important;
        color: #e6edf3 !important;
    }
</style>
"""


# =========================================================================
# Settings tab renderer
# =========================================================================
def render_settings_tab():
    st.subheader("Settings")
    s = load_settings()
    c1, c2 = st.columns(2)
    with c1:
        cur_sid = s.get("steam_id") or os.getenv("STEAM_ID", "")
        new_sid = st.text_input("Steam ID (64-bit)", value=cur_sid,
                                placeholder="76561198012345678",
                                help="Get yours from steamid.io")
    with c2:
        cur_cf = s.get("csfloat_api_key") or os.getenv("CSFLOAT_API_KEY", "")
        new_cf = st.text_input("CSFloat API key (optional)", value=cur_cf,
                               type="password",
                               help="Enables CSFloat pricing alongside Steam Market")
    if st.button("Save settings"):
        save_settings({"steam_id": new_sid.strip(), "csfloat_api_key": new_cf.strip()})
        _invalidate_fetch_cache()
        st.cache_data.clear()
        st.success("Saved!")
        st.rerun()
    st.divider()
    n = len(get_watchlist())
    sc1, sc2, sc3 = st.columns(3)
    sc1.metric("Steam ID", "Connected" if get_steam_id() else "Not set")
    sc2.metric("CSFloat key", "Connected" if get_csfloat_key() else "Not set")
    sc3.metric("Auto-refresh", f"{_auto_cache_ttl(n) // 60} min")


# =========================================================================
# Main
# =========================================================================
def main():
    st.set_page_config(page_title="CS2 Inventory Tracker", page_icon="🎯",
                       layout="wide", initial_sidebar_state="collapsed")
    st.markdown(CSS, unsafe_allow_html=True)

    steam_id = get_steam_id()
    watchlist = get_watchlist()
    watchlist_set = set(watchlist)

    tab_dash, tab_inv, tab_manage, tab_settings = st.tabs(["Portfolio", "Inventory", "Watchlist", "Settings"])

    # ── Dashboard ─────────────────────────────────────────────
    with tab_dash:
        if not watchlist:
            st.info("Your watchlist is empty. Go to the **Inventory** tab to browse and add items, "
                    "or use the **Watchlist** tab to add them manually.")
        else:
            cache_ttl = _auto_cache_ttl(len(watchlist))

            # Use _cache_bust to force refetch when TTL expires or user clicks refresh
            ts_data = _read_json(LAST_FETCH_FILE)
            last_ts = ts_data.get("ts", 0)
            elapsed = time.time() - last_ts if last_ts else 999999
            cache_bust = int(last_ts) if elapsed < cache_ttl else int(time.time())

            rows, warnings = fetch_watchlist_data(tuple(watchlist), steam_id, _cache_bust=cache_bust)
            for w in warnings:
                st.warning(w)

            if rows:
                total_qty = sum(r["qty"] for r in rows)

                # Steam totals
                steam_total = 0.0
                steam_prev_total = 0.0
                for r in rows:
                    sp = r.get("steam_price")
                    q = r.get("qty", 0)
                    if sp is not None and q > 0:
                        steam_total += sp * q
                        sd = r.get("steam_delta")
                        if sd is not None:
                            steam_prev_total += (sp - sd) * q
                        else:
                            steam_prev_total += sp * q
                steam_delta = round(steam_total - steam_prev_total, 2) if steam_prev_total > 0 else None
                steam_pct = round((steam_delta / steam_prev_total) * 100, 1) if steam_delta and steam_prev_total else None
                steam_badge = _portfolio_change_html(steam_delta, steam_pct)

                # CSFloat totals
                cf_total = 0.0
                cf_prev_total = 0.0
                for r in rows:
                    cp = r.get("cf_price")
                    q = r.get("qty", 0)
                    if cp is not None and q > 0:
                        cf_total += cp * q
                        cd = r.get("cf_delta")
                        if cd is not None:
                            cf_prev_total += (cp - cd) * q
                        else:
                            cf_prev_total += cp * q
                cf_delta = round(cf_total - cf_prev_total, 2) if cf_prev_total > 0 else None
                cf_pct = round((cf_delta / cf_prev_total) * 100, 1) if cf_delta and cf_prev_total else None
                cf_badge = _portfolio_change_html(cf_delta, cf_pct)

                next_refresh = max(0, int(cache_ttl - elapsed))
                next_str = f"{next_refresh // 60}m {next_refresh % 60}s" if next_refresh > 0 else "now"
                last_str = time.strftime("%H:%M UTC", time.gmtime())

                steam_val_str = f"${steam_total:,.2f}" if steam_total else "—"
                cf_val_str = f"${cf_total:,.2f}" if cf_total else "—"

                st.markdown(
                    f'<div class="stats-bar">'
                    f'<div class="stats-bar-left">'
                    f'<div class="ticker-item"><span class="ticker-label">Steam Value</span><span class="ticker-value">{steam_val_str}</span></div>'
                    f'<div class="ticker-item ticker-item-change">{steam_badge}</div>'
                    f'<div class="ticker-sep"></div>'
                    f'<div class="ticker-item"><span class="ticker-label">Float Value</span><span class="ticker-value">{cf_val_str}</span></div>'
                    f'<div class="ticker-item ticker-item-change">{cf_badge}</div>'
                    f'<div class="ticker-sep"></div>'
                    f'<div class="ticker-item"><span class="ticker-label">Items</span><span class="ticker-value">{len(rows)}</span></div>'
                    f'<div class="ticker-item"><span class="ticker-label">Qty</span><span class="ticker-value">{total_qty}</span></div>'
                    f'</div>'
                    f'<div class="stats-bar-right"><span class="stats-bar-meta">Next {next_str} · Last {last_str}</span></div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                if st.button("🔄 Refresh prices", use_container_width=False):
                    _invalidate_fetch_cache()
                    st.cache_data.clear()
                    st.rerun()

                # ── Trading card grid (4 per row on wide screens) ──
                n_cols = 4
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
            st.warning("Go to the **Settings** tab and enter your **Steam 64-bit ID** to load your inventory.")
        else:
            inv = get_inventory_items(steam_id)
            if not inv:
                st.warning("Could not load inventory. Make sure your **Steam profile** and **CS2 inventory** are set to **Public**.")
            else:
                st.caption(f"{len(inv)} unique items in your inventory")
                search = st.text_input("🔍 Search", placeholder="Filter by name…", key="inv_search")
                filtered = [i for i in inv if search.lower() in i["name"].lower()] if search else inv

                unwatched = [i for i in filtered if i["name"] not in watchlist_set]
                watched_count = len(filtered) - len(unwatched)

                # Collect checkbox selections
                selected: list[str] = []
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
                                st.markdown(
                                    '<span style="color:#3fb950;font-size:0.78rem;">⭐ Tracked</span>',
                                    unsafe_allow_html=True,
                                )
                            else:
                                if st.checkbox("Select", key=f"isel_{idx}", label_visibility="visible"):
                                    selected.append(it["name"])
                            st.markdown('</div>', unsafe_allow_html=True)

                # Sticky action bar at the top (rendered after checkboxes so we know the count)
                if selected:
                    st.toast(f"{len(selected)} items selected")
                act1, act2, act3 = st.columns([3, 2, 2])
                with act1:
                    st.caption(f"Showing {len(filtered)} items · {watched_count} tracked · {len(selected)} selected")
                with act2:
                    if selected:
                        if st.button(f"Add {len(selected)} selected to watchlist", key="add_selected",
                                     type="primary", use_container_width=True):
                            cur = get_watchlist()
                            cur_set = set(cur)
                            new_items = [n for n in selected if n not in cur_set]
                            if new_items:
                                save_watchlist(cur + new_items)
                            st.rerun()
                with act3:
                    if unwatched:
                        if st.button(f"Add all {len(unwatched)}", key="bulk_add", use_container_width=True):
                            cur = get_watchlist()
                            cur_set = set(cur)
                            new_items = [i["name"] for i in unwatched if i["name"] not in cur_set]
                            if new_items:
                                save_watchlist(cur + new_items)
                            st.rerun()

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
            st.info("Watchlist is empty. Add items from the **Inventory** tab or type a name below.")
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

    # ── Settings ──────────────────────────────────────────────
    with tab_settings:
        render_settings_tab()


if __name__ == "__main__":
    main()
