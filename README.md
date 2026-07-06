# Market Screener (TradingView-backed)

A basic stock screener that pulls live data from TradingView's public scanner endpoint.
UI matches the existing Market Dashboard mockups.

## Run

```bash
pip install -r requirements.txt
uvicorn server:app --reload
```

Open <http://localhost:8000>.

## Deploy (Render, free)

See [DEPLOY.md](./DEPLOY.md) for a step-by-step walkthrough — takes about
10 minutes and results in a public URL like `https://market-screener.onrender.com`.

## Architecture

```
browser  ──GET /api/screener──▶  FastAPI (server.py)
                                      │
                                      ▼
                      POST https://scanner.tradingview.com/{market}/scan
                                      │
                                      ▼
                              JSON: { totalCount, data: [...] }
```

The backend exists purely to bypass CORS and normalize the response shape — TradingView's
scanner refuses cross-origin requests from the browser but is fine with server-side POSTs.

## API

- `GET /api/markets` — returns the supported markets map
- `GET /api/screener` — query params:
  - `market` (default `america`)
  - `min_market_cap`, `min_price`
  - `min_change_1d`, `max_change_1d`
  - `min_change_7d`, `max_change_7d`
  - `sector`
  - `sort_by` (default `market_cap_basic`), `ascending` (default `false`)
  - `limit` (1–200, default 50)

## Extending

**More columns:** add TradingView field names to the `COLUMNS` list in `server.py` and
surface them in the table in `index.html`. The full column vocabulary is large
(hundreds of fields — fundamentals, technicals, ratings, dividends). Useful examples:

| Field | Meaning |
| --- | --- |
| `RSI` | Relative Strength Index (14) |
| `Perf.1M`, `Perf.3M`, `Perf.Y` | 1-month / 3-month / 1-year performance % |
| `price_earnings_ttm` | P/E ratio |
| `dividend_yield_recent` | Dividend yield % |
| `high`, `low` | Daily high / low |
| `Volatility.W` | Weekly volatility |
| `Recommend.All` | Aggregate technical rating (−1 to +1) |

**More filter operations:** the scanner supports `egreater`, `eless`, `greater`, `less`,
`equal`, `nequal`, `in_range`, `not_in_range`, `match` (text contains), `crosses`,
`above%`, `below%`, and others.

**More markets:** add entries to the `MARKETS` dict. Any TradingView-supported market
string works (e.g. `singapore`, `switzerland`, `mexico`, `forex`, `futures`).

## Caveats

- The scanner endpoint is public but unofficial. TradingView may change its shape or
  rate-limit it without notice.
- Intraday data lags real-time by ~15 minutes on most exchanges, matching TradingView's
  free-tier delay.
- 7-day $ change is derived from the 7-day % and current close (TradingView only
  exposes the percentage directly).
