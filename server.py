"""
TradingView-powered screener backend.

Proxies the (unofficial but stable) scanner.tradingview.com endpoint so the
browser can bypass CORS, and exposes a clean REST API the frontend consumes.

Run:
    pip install -r requirements.txt
    uvicorn server:app --reload
Open:
    http://localhost:8000
"""

from __future__ import annotations

import datetime as dt
import os
import pathlib
from typing import Any, Optional

import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

HERE = pathlib.Path(__file__).parent

app = FastAPI(title="TradingView Screener")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Markets exposed in the UI. TradingView supports ~70; this is a curated subset.
MARKETS: dict[str, str] = {
    "america": "US",
    "uk": "United Kingdom",
    "germany": "Germany",
    "france": "France",
    "japan": "Japan",
    "hongkong": "Hong Kong",
    "india": "India",
    "canada": "Canada",
    "australia": "Australia",
    "brazil": "Brazil",
    "crypto": "Crypto",
}

# Columns requested from TradingView, in order. Response "d" arrays map 1:1 to this.
COLUMNS: list[str] = [
    "name",              # ticker
    "description",       # company name
    "close",             # last price
    "change",            # 1D %
    "change_abs",        # 1D $
    "Perf.W",            # 7D %
    "Perf.1M",           # ~20D % (TradingView only exposes calendar-month, not 20-trading-day)
    "RSI",               # Relative Strength Index, 14-period daily
    "price_earnings_ttm",         # P/E (trailing 12 months)
    "price_sales_current",        # P/S
    "price_earnings_growth_ttm",  # PEG (trailing; null for many names)
    "volume",
    "market_cap_basic",
    "sector",
    "industry",
    "exchange",
]

SCANNER_URL = "https://scanner.tradingview.com/{market}/scan"
TIMEOUT = 10


def _build_filters(
    min_market_cap: Optional[float],
    min_price: Optional[float],
    min_change_1d: Optional[float],
    max_change_1d: Optional[float],
    min_change_7d: Optional[float],
    max_change_7d: Optional[float],
    min_change_20d: Optional[float],
    max_change_20d: Optional[float],
    sector: Optional[str],
    min_volume: Optional[float] = None,
) -> list[dict[str, Any]]:
    filters: list[dict[str, Any]] = []

    def push(col: str, op: str, val: Any) -> None:
        filters.append({"left": col, "operation": op, "right": val})

    if min_market_cap is not None:
        push("market_cap_basic", "egreater", min_market_cap)
    if min_price is not None:
        push("close", "egreater", min_price)
    if min_change_1d is not None:
        push("change", "egreater", min_change_1d)
    if max_change_1d is not None:
        push("change", "eless", max_change_1d)
    if min_change_7d is not None:
        push("Perf.W", "egreater", min_change_7d)
    if max_change_7d is not None:
        push("Perf.W", "eless", max_change_7d)
    if min_change_20d is not None:
        push("Perf.1M", "egreater", min_change_20d)
    if max_change_20d is not None:
        push("Perf.1M", "eless", max_change_20d)
    if sector:
        push("sector", "equal", sector)
    if min_volume is not None and min_volume > 0:
        push("volume", "egreater", min_volume)

    return filters


def _normalize_row(symbol: str, values: list[Any]) -> dict[str, Any]:
    """Map the positional `d` array back to named fields, plus derived metrics."""
    row = dict(zip(COLUMNS, values))
    row["symbol"] = symbol  # e.g. NASDAQ:AAPL

    close = row.get("close")
    pct_7d = row.get("Perf.W")
    pct_20d = row.get("Perf.1M")
    # Derive $ change from % and current close:
    #   if current = C and %change = p, then prev = C / (1 + p/100),
    #   so $ change = C * p / (100 + p)
    if close is not None and pct_7d is not None and (100 + pct_7d) != 0:
        row["change_abs_7d"] = close * pct_7d / (100 + pct_7d)
    else:
        row["change_abs_7d"] = None

    if close is not None and pct_20d is not None and (100 + pct_20d) != 0:
        row["change_abs_20d"] = close * pct_20d / (100 + pct_20d)
    else:
        row["change_abs_20d"] = None

    return row


@app.get("/api/markets")
def list_markets() -> dict[str, str]:
    return MARKETS


@app.get("/api/screener")
def screener(
    market: str = Query("america"),
    min_market_cap: Optional[float] = None,
    min_price: Optional[float] = None,
    min_change_1d: Optional[float] = None,
    max_change_1d: Optional[float] = None,
    min_change_7d: Optional[float] = None,
    max_change_7d: Optional[float] = None,
    min_change_20d: Optional[float] = None,
    max_change_20d: Optional[float] = None,
    sector: Optional[str] = None,
    min_volume: float = Query(1_000_000, description="Minimum share volume; default 1M"),
    sort_by: str = Query("market_cap_basic"),
    ascending: bool = False,
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    if market not in MARKETS:
        raise HTTPException(400, f"Unknown market: {market}")

    payload: dict[str, Any] = {
        "filter": _build_filters(
            min_market_cap, min_price,
            min_change_1d, max_change_1d,
            min_change_7d, max_change_7d,
            min_change_20d, max_change_20d,
            sector, min_volume,
        ),
        "options": {"lang": "en"},
        "symbols": {"query": {"types": []}, "tickers": []},
        "columns": COLUMNS,
        "sort": {
            "sortBy": sort_by,
            "sortOrder": "asc" if ascending else "desc",
        },
        "range": [0, limit],
    }

    try:
        r = requests.post(
            SCANNER_URL.format(market=market),
            json=payload,
            timeout=TIMEOUT,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json",
            },
        )
        r.raise_for_status()
    except requests.RequestException as e:
        raise HTTPException(502, f"Upstream TradingView error: {e}")

    body = r.json()
    raw_rows = body.get("data") or []
    rows = [_normalize_row(item["s"], item["d"]) for item in raw_rows]

    return {
        "total": body.get("totalCount", len(rows)),
        "count": len(rows),
        "market": market,
        "rows": rows,
    }



# Serve the frontend alongside the API so this is one self-contained process.
@app.get("/")
def index() -> FileResponse:
    return FileResponse(HERE / "index.html")


@app.get("/trends")
def trends() -> FileResponse:
    return FileResponse(HERE / "trends.html")


CNN_FNG_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"


# Fields needed for sector aggregation — a narrower subset than the stock scanner.
SECTOR_AGG_COLUMNS: list[str] = [
    "sector",
    "change",
    "Perf.W",
    "Perf.1M",
    "market_cap_basic",
    "price_earnings_ttm",
    "price_sales_current",   # P/S
    "price_book_ratio",      # P/B
    "net_margin",            # net profit margin (as a fraction, e.g. 0.26)
]


def _median(xs: list[float]) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2


@app.get("/api/sectors")
def sectors(
    market: str = Query("america"),
    min_volume: float = Query(1_000_000),
) -> dict[str, Any]:
    """Aggregate stock-level changes into mean 1D / 7D / 20D per sector.

    We pull a large slice of the market (sorted by market cap so the sample is
    biased toward meaningful names), then group-by sector in Python.
    """
    if market not in MARKETS:
        raise HTTPException(400, f"Unknown market: {market}")

    payload: dict[str, Any] = {
        "filter": [
            {"left": "volume", "operation": "egreater", "right": min_volume},
        ],
        "options": {"lang": "en"},
        "symbols": {"query": {"types": []}, "tickers": []},
        "columns": SECTOR_AGG_COLUMNS,
        "sort": {"sortBy": "market_cap_basic", "sortOrder": "desc"},
        "range": [0, 2000],  # scanner caps around this
    }

    try:
        r = requests.post(
            SCANNER_URL.format(market=market),
            json=payload,
            timeout=TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
        )
        r.raise_for_status()
    except requests.RequestException as e:
        raise HTTPException(502, f"Upstream TradingView error: {e}")

    raw = r.json().get("data") or []

    # Group into sector buckets. Skip rows with missing sector or all-missing changes.
    buckets: dict[str, dict[str, list[float]]] = {}
    for item in raw:
        vals = dict(zip(SECTOR_AGG_COLUMNS, item["d"]))
        sec = vals.get("sector")
        if not sec:
            continue
        b = buckets.setdefault(sec, {
            "change": [], "Perf.W": [], "Perf.1M": [], "mcap": [],
            "pe": [], "ps": [], "pb": [], "nm": [],
        })
        for key in ("change", "Perf.W", "Perf.1M"):
            v = vals.get(key)
            if v is not None:
                b[key].append(v)
        mc = vals.get("market_cap_basic")
        if mc is not None:
            b["mcap"].append(mc)
        pe = vals.get("price_earnings_ttm")
        if pe is not None and 0 < pe < 500:  # drop negatives and absurd outliers
            b["pe"].append(pe)
        ps = vals.get("price_sales_current")
        if ps is not None and 0 < ps < 200:
            b["ps"].append(ps)
        pb = vals.get("price_book_ratio")
        if pb is not None and 0 < pb < 200:
            b["pb"].append(pb)
        nm = vals.get("net_margin")
        if nm is not None and -5 < nm < 5:  # fraction; filters garbage outliers
            b["nm"].append(nm)

    def mean(xs: list[float]) -> Optional[float]:
        return sum(xs) / len(xs) if xs else None

    rows = []
    for sec, b in buckets.items():
        rows.append({
            "sector": sec,
            "count": len(b["mcap"]) or max(len(b["change"]), len(b["Perf.W"]), len(b["Perf.1M"])),
            "mean_1d":  mean(b["change"]),
            "mean_7d":  mean(b["Perf.W"]),
            "mean_20d": mean(b["Perf.1M"]),
            "median_pe": _median(b["pe"]),
            "median_ps": _median(b["ps"]),
            "median_pb": _median(b["pb"]),
            "median_net_margin": _median(b["nm"]),
            "total_market_cap": sum(b["mcap"]) if b["mcap"] else None,
        })

    # Sort by total market cap descending so the biggest sectors come first by default.
    rows.sort(key=lambda r: r["total_market_cap"] or 0, reverse=True)

    return {"market": market, "count": len(rows), "total": len(rows), "rows": rows}


@app.get("/api/fear-greed")
def fear_greed() -> dict[str, Any]:
    """Proxy CNN's Fear & Greed Index. Unofficial but widely used endpoint."""
    try:
        r = requests.get(
            CNN_FNG_URL,
            timeout=TIMEOUT,
            headers={
                # CNN returns 418 without a browser-like UA.
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Origin": "https://edition.cnn.com",
                "Referer": "https://edition.cnn.com/",
            },
        )
        r.raise_for_status()
    except requests.RequestException as e:
        raise HTTPException(502, f"Upstream CNN error: {e}")

    body = r.json()
    fng = body.get("fear_and_greed", {})
    return {
        "score": fng.get("score"),
        "rating": fng.get("rating"),          # extreme fear | fear | neutral | greed | extreme greed
        "timestamp": fng.get("timestamp"),
        "previous_close": fng.get("previous_close"),
        "previous_1_week": fng.get("previous_1_week"),
        "previous_1_month": fng.get("previous_1_month"),
        "previous_1_year": fng.get("previous_1_year"),
    }


# ======================================================================
# Phase 1 & 3: FMP-powered stock detail + support/resistance levels
# ======================================================================
#
# Both endpoints require a free Financial Modeling Prep API key:
#   https://site.financialmodelingprep.com/developer/docs  (~250 req/day free)
# Set it as the FMP_API_KEY environment variable. Without it, the endpoints
# return {"available": false} and the frontend degrades gracefully.

FMP_API_KEY = os.environ.get("FMP_API_KEY", "")
FMP_BASE = "https://financialmodelingprep.com/stable"

# Day-scoped in-memory cache: {(kind:ticker, YYYY-MM-DD): payload}.
# Daily fundamentals and daily candles don't change intraday, so caching
# until midnight both speeds up repeat clicks and conserves the FMP budget.
_day_cache: dict[tuple[str, str], Any] = {}


def _cache_key(kind: str, ticker: str) -> tuple[str, str]:
    return (f"{kind}:{ticker.upper()}", dt.date.today().isoformat())


def _fmp_get(path: str, params: dict[str, Any] | None = None) -> Any:
    p = dict(params or {})
    p["apikey"] = FMP_API_KEY
    r = requests.get(f"{FMP_BASE}/{path}", params=p, timeout=15)
    r.raise_for_status()
    data = r.json()
    # FMP sometimes returns HTTP 200 with an error payload instead of a
    # proper 4xx (e.g. plan restrictions, bad symbol). Surface it clearly.
    if isinstance(data, dict) and ("Error Message" in data or "error" in data):
        raise requests.RequestException(data.get("Error Message") or data.get("error"))
    return data


@app.get("/api/stock/{ticker}/detail")
def stock_detail(ticker: str) -> dict[str, Any]:
    """FMP enrichment for the expand panel: margins, debt, beta, dividend.

    Uses 2 FMP calls per ticker per day (ratios-ttm + profile), cached.
    """
    ticker = ticker.upper()
    if not FMP_API_KEY:
        return {"available": False, "reason": "FMP_API_KEY not configured"}

    key = _cache_key("detail", ticker)
    if key in _day_cache:
        return _day_cache[key]

    try:
        ratios_list = _fmp_get("ratios-ttm", {"symbol": ticker})
        profile_list = _fmp_get("profile", {"symbol": ticker})
    except requests.RequestException as e:
        raise HTTPException(502, f"Upstream FMP error: {e}")

    ratios = ratios_list[0] if ratios_list else {}
    profile = profile_list[0] if profile_list else {}

    def pick(d: dict, *keys: str):
        """Return the first present, non-None value among candidate field
        names. FMP has renamed fields across API versions before; this
        keeps us working through minor drift without another silent break."""
        for k in keys:
            if k in d and d[k] is not None:
                return d[k]
        return None

    result = {
        "available": True,
        "ticker": ticker,
        # Valuation (FMP's view; can differ slightly from TradingView's)
        "pe_ttm": pick(ratios, "peRatioTTM", "priceToEarningsRatioTTM"),
        "peg_ttm": pick(ratios, "pegRatioTTM", "priceEarningsToGrowthRatioTTM"),
        "price_to_sales_ttm": pick(ratios, "priceToSalesRatioTTM"),
        "price_to_book_ttm": pick(ratios, "priceToBookRatioTTM"),
        "price_to_fcf_ttm": pick(ratios, "priceToFreeCashFlowsRatioTTM"),
        # Profitability & balance sheet quality
        "net_margin_ttm": pick(ratios, "netProfitMarginTTM", "netIncomePerEBTTTM"),
        "gross_margin_ttm": pick(ratios, "grossProfitMarginTTM"),
        "roe_ttm": pick(ratios, "returnOnEquityTTM"),
        "debt_to_equity_ttm": pick(ratios, "debtEquityRatioTTM", "debtToEquityRatioTTM"),
        "current_ratio_ttm": pick(ratios, "currentRatioTTM"),
        "dividend_yield_ttm": pick(ratios, "dividendYielTTM", "dividendYieldTTM"),
        # Profile
        "beta": profile.get("beta"),
        "industry": profile.get("industry"),
        "employees": profile.get("fullTimeEmployees"),
        "description": profile.get("description"),
        "website": profile.get("website"),
    }
    _day_cache[key] = result
    return result


# ---- Support / resistance from swing-point clustering ----------------

def _find_swings(candles: list[dict], k: int = 3) -> tuple[list[float], list[float]]:
    """A bar is a swing high if its high is the max of the k bars on each
    side (swing low symmetric). Classic fractal definition."""
    highs, lows = [], []
    n = len(candles)
    for i in range(k, n - k):
        window = candles[i - k: i + k + 1]
        hi = candles[i]["high"]
        lo = candles[i]["low"]
        if hi == max(c["high"] for c in window):
            highs.append((i, hi))
        if lo == min(c["low"] for c in window):
            lows.append((i, lo))
    return highs, lows


def _cluster_levels(swings: list[tuple[int, float]], tolerance: float, n_bars: int) -> list[dict]:
    """Merge swing points within `tolerance` (fraction of price) into zones.
    Zone strength = number of touches, boosted for recent touches."""
    if not swings:
        return []
    pts = sorted(swings, key=lambda t: t[1])
    clusters: list[list[tuple[int, float]]] = [[pts[0]]]
    for idx, price in pts[1:]:
        center = sum(p for _, p in clusters[-1]) / len(clusters[-1])
        if abs(price - center) / center <= tolerance:
            clusters[-1].append((idx, price))
        else:
            clusters.append([(idx, price)])

    zones = []
    for c in clusters:
        center = sum(p for _, p in c) / len(c)
        touches = len(c)
        # Recency boost: touches in the most recent quarter of the window count extra
        recent = sum(1 for i, _ in c if i >= n_bars * 0.75)
        zones.append({
            "level": round(center, 2),
            "touches": touches,
            "recent_touches": recent,
            "strength": touches + 0.5 * recent,
        })
    return zones


def _atr(candles: list[dict], period: int = 14) -> Optional[float]:
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        h, l = candles[i]["high"], candles[i]["low"]
        pc = candles[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    recent = trs[-period:]
    return sum(recent) / len(recent)


@app.get("/api/stock/{ticker}/levels")
def stock_levels(ticker: str) -> dict[str, Any]:
    """Support/resistance zones from ~1 year of daily candles, plus ATR and
    a risk/reward ratio at the current price.

    Uses 1 FMP call per ticker per day, cached.
    """
    ticker = ticker.upper()
    if not FMP_API_KEY:
        return {"available": False, "reason": "FMP_API_KEY not configured"}

    key = _cache_key("levels", ticker)
    if key in _day_cache:
        return _day_cache[key]

    today = dt.date.today()
    frm = (today - dt.timedelta(days=365)).isoformat()
    try:
        data = _fmp_get(
            "historical-price-eod/full",
            {"symbol": ticker, "from": frm, "to": today.isoformat()},
        )
    except requests.RequestException as e:
        raise HTTPException(502, f"Upstream FMP error: {e}")

    # The stable API returns a flat list of daily bars directly. Some legacy
    # responses (or future changes) wrap it as {"historical": [...]} — handle both.
    hist = data if isinstance(data, list) else (data.get("historical") or [])
    if len(hist) < 40:
        return {"available": False, "reason": "Not enough price history"}

    # Sort chronologically by date rather than trusting the API's order,
    # since that has changed between FMP API versions before.
    candles = sorted(
        (
            {"high": h["high"], "low": h["low"], "close": h["close"], "date": h["date"]}
            for h in hist
        ),
        key=lambda c: c["date"],
    )
    n = len(candles)
    price = candles[-1]["close"]

    swing_highs, swing_lows = _find_swings(candles, k=3)
    # Tolerance scales a little with volatility; 1.5% default.
    high_zones = _cluster_levels(swing_highs, 0.015, n)
    low_zones = _cluster_levels(swing_lows, 0.015, n)

    # Nearest resistance above price / support below, preferring stronger zones
    # when two are close together (within 2%).
    def pick(zones: list[dict], above: bool) -> Optional[dict]:
        side = [z for z in zones if (z["level"] > price) == above and z["level"] != price]
        if not side:
            return None
        side.sort(key=lambda z: abs(z["level"] - price))
        best = side[0]
        for z in side[1:3]:
            if abs(z["level"] - best["level"]) / price < 0.02 and z["strength"] > best["strength"]:
                best = z
        return best

    resistance = pick(high_zones + low_zones, above=True)   # old support can act as resistance
    support = pick(low_zones + high_zones, above=False)     # and vice versa

    # Fall back to 52-week extremes if no swing zone exists on a side
    hi_52w = max(c["high"] for c in candles)
    lo_52w = min(c["low"] for c in candles)
    if resistance is None and hi_52w > price:
        resistance = {"level": round(hi_52w, 2), "touches": 1, "recent_touches": 0,
                      "strength": 1, "fallback": "52w high"}
    if support is None and lo_52w < price:
        support = {"level": round(lo_52w, 2), "touches": 1, "recent_touches": 0,
                   "strength": 1, "fallback": "52w low"}

    rr = None
    if resistance and support:
        upside = resistance["level"] - price
        downside = price - support["level"]
        if downside > 0:
            rr = round(upside / downside, 2)

    atr = _atr(candles)
    result = {
        "available": True,
        "ticker": ticker,
        "price": price,
        "as_of": candles[-1]["date"],
        "support": support,
        "resistance": resistance,
        "risk_reward": rr,
        "atr_14": round(atr, 2) if atr else None,
        "atr_pct": round(atr / price * 100, 2) if atr and price else None,
        "week52_high": round(hi_52w, 2),
        "week52_low": round(lo_52w, 2),
        "candles_used": n,
        "note": "Zones from swing-point clustering over ~1y of daily bars. "
                "Heuristic screening aid, not a prediction.",
    }
    _day_cache[key] = result
    return result
