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


@app.get("/api/sectors")
def sectors(
    market: str = Query("america"),
    min_change_1d: Optional[float] = None,
    max_change_1d: Optional[float] = None,
    min_change_7d: Optional[float] = None,
    max_change_7d: Optional[float] = None,
    min_change_20d: Optional[float] = None,
    max_change_20d: Optional[float] = None,
    min_volume: float = Query(1_000_000),
    sort_by: str = Query("mean_1d"),
    ascending: bool = False,
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    """Aggregate liquid stocks by sector and return mean 1D/7D/20D changes."""
    if market not in MARKETS:
        raise HTTPException(400, f"Unknown market: {market}")

    # Fetch a wide net of liquid stocks (top ~1500 by mcap), then group.
    payload: dict[str, Any] = {
        "filter": [
            {"left": "volume", "operation": "egreater", "right": min_volume},
            {"left": "market_cap_basic", "operation": "egreater", "right": 100_000_000},
        ],
        "options": {"lang": "en"},
        "symbols": {"query": {"types": []}, "tickers": []},
        "columns": ["sector", "change", "Perf.W", "Perf.1M"],
        "sort": {"sortBy": "market_cap_basic", "sortOrder": "desc"},
        "range": [0, 1500],
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

    raw_rows = r.json().get("data") or []

    # Group stocks by sector.
    groups: dict[str, dict[str, list[float]]] = {}
    for item in raw_rows:
        d = item.get("d") or []
        if len(d) < 4:
            continue
        sector, change, perf_w, perf_m = d
        if not sector:
            continue
        g = groups.setdefault(sector, {"change": [], "perf_w": [], "perf_m": []})
        if change is not None: g["change"].append(change)
        if perf_w is not None: g["perf_w"].append(perf_w)
        if perf_m is not None: g["perf_m"].append(perf_m)

    def mean(a: list[float]) -> Optional[float]:
        return sum(a) / len(a) if a else None

    sector_rows: list[dict[str, Any]] = []
    for name, arrays in groups.items():
        sector_rows.append({
            "sector": name,
            "count": len(arrays["change"]),
            "mean_1d": mean(arrays["change"]),
            "mean_7d": mean(arrays["perf_w"]),
            "mean_20d": mean(arrays["perf_m"]),
        })

    # Apply filters on the aggregated means.
    def passes(s: dict[str, Any]) -> bool:
        checks = [
            ("mean_1d", min_change_1d, "min"), ("mean_1d", max_change_1d, "max"),
            ("mean_7d", min_change_7d, "min"), ("mean_7d", max_change_7d, "max"),
            ("mean_20d", min_change_20d, "min"), ("mean_20d", max_change_20d, "max"),
        ]
        for col, bound, kind in checks:
            if bound is None:
                continue
            v = s.get(col)
            if v is None:
                return False
            if kind == "min" and v < bound:
                return False
            if kind == "max" and v > bound:
                return False
        return True

    sector_rows = [s for s in sector_rows if passes(s)]

    # Sort; None values sink to the bottom regardless of direction.
    def sort_key(s: dict[str, Any]) -> float:
        v = s.get(sort_by)
        if v is None:
            return float("inf") if ascending else float("-inf")
        return v

    sector_rows.sort(key=sort_key, reverse=not ascending)

    return {
        "total": len(sector_rows),
        "count": min(len(sector_rows), limit),
        "market": market,
        "rows": sector_rows[:limit],
    }


@app.get("/api/sectors")
def sectors(
    market: str = Query("america"),
    min_change_1d: Optional[float] = None,
    max_change_1d: Optional[float] = None,
    min_change_7d: Optional[float] = None,
    max_change_7d: Optional[float] = None,
    min_change_20d: Optional[float] = None,
    max_change_20d: Optional[float] = None,
    sort_by: str = Query("change"),
    ascending: bool = False,
    min_volume: float = 1_000_000,
) -> dict[str, Any]:
    """Aggregate mean 1D/7D/20D change per sector.

    Approach: pull a large set of stocks in the chosen market (volume-filtered),
    group by `sector`, compute means, then apply the user's change filters on the
    aggregated numbers and sort.
    """
    if market not in MARKETS:
        raise HTTPException(400, f"Unknown market: {market}")

    # Pull a wide slice for robust averages. 1000 rows is plenty and fast.
    payload = {
        "filter": [{"left": "volume", "operation": "egreater", "right": min_volume}],
        "options": {"lang": "en"},
        "symbols": {"query": {"types": []}, "tickers": []},
        "columns": ["sector", "change", "Perf.W", "Perf.1M", "market_cap_basic"],
        "sort": {"sortBy": "market_cap_basic", "sortOrder": "desc"},
        "range": [0, 1000],
    }
    try:
        r = requests.post(
            SCANNER_URL.format(market=market), json=payload, timeout=TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
        )
        r.raise_for_status()
    except requests.RequestException as e:
        raise HTTPException(502, f"Upstream TradingView error: {e}")

    raw_rows = r.json().get("data") or []

    # Group and average. Weight equally (arithmetic mean of % changes across stocks
    # in each sector). Market-cap-weighted would also be reasonable and is a trivial
    # extension if we want it later.
    agg: dict[str, dict[str, Any]] = {}
    for item in raw_rows:
        d = item["d"]
        sector, ch, w, m, mc = d[0], d[1], d[2], d[3], d[4]
        if not sector:
            continue
        bucket = agg.setdefault(sector, {
            "count": 0, "change": 0.0, "Perf.W": 0.0, "Perf.1M": 0.0, "market_cap": 0.0,
        })
        bucket["count"] += 1
        if ch is not None:      bucket["change"] += ch
        if w is not None:       bucket["Perf.W"] += w
        if m is not None:       bucket["Perf.1M"] += m
        if mc is not None:      bucket["market_cap"] += mc

    sector_rows = []
    for sector, b in agg.items():
        n = b["count"]
        if n == 0: continue
        sector_rows.append({
            "sector": sector,
            "count": n,
            "change": b["change"] / n,
            "Perf.W": b["Perf.W"] / n,
            "Perf.1M": b["Perf.1M"] / n,
            "market_cap": b["market_cap"],  # total, not averaged
        })

    # Apply the user's change filters on aggregated values
    def keep(row: dict[str, Any]) -> bool:
        if min_change_1d is not None and row["change"] < min_change_1d: return False
        if max_change_1d is not None and row["change"] > max_change_1d: return False
        if min_change_7d is not None and row["Perf.W"] < min_change_7d: return False
        if max_change_7d is not None and row["Perf.W"] > max_change_7d: return False
        if min_change_20d is not None and row["Perf.1M"] < min_change_20d: return False
        if max_change_20d is not None and row["Perf.1M"] > max_change_20d: return False
        return True

    sector_rows = [r for r in sector_rows if keep(r)]

    # Sort. Accept the same column keys as /api/screener for consistency.
    sort_key_map = {"change": "change", "Perf.W": "Perf.W", "Perf.1M": "Perf.1M"}
    key = sort_key_map.get(sort_by, "change")
    sector_rows.sort(key=lambda x: (x.get(key) is None, x.get(key)), reverse=not ascending)

    return {"market": market, "count": len(sector_rows), "rows": sector_rows}


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
]


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
        b = buckets.setdefault(sec, {"change": [], "Perf.W": [], "Perf.1M": [], "mcap": []})
        for key in ("change", "Perf.W", "Perf.1M"):
            v = vals.get(key)
            if v is not None:
                b[key].append(v)
        mc = vals.get("market_cap_basic")
        if mc is not None:
            b["mcap"].append(mc)

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
