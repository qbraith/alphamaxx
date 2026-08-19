"""
backfill_prices.py — Download full price history for portfolio/watchlist tickers missing data.
Run this once with the app stopped: python3 scripts/backfill_prices.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import yfinance as yf

from alphamaxx.data import get_conn, init_db
from alphamaxx.data.momentum import refresh_momentum

import duckdb

try:
    init_db()
    con = get_conn()
except duckdb.IOException as e:
    sys.exit(f"Cannot open alphamaxx.db ({e}).\n"
             "The app server is probably running — stop it first "
             "(DuckDB allows one writer process).")

# Find all portfolio/watchlist tickers with fewer than 10 price rows
missing = con.execute("""
    SELECT c.permno, c.ticker
    FROM companies c
    LEFT JOIN (
        SELECT permno, COUNT(*) as cnt FROM prices GROUP BY permno
    ) p ON c.permno = p.permno
    WHERE (c.in_portfolio = TRUE OR c.in_watchlist = TRUE)
      AND (p.cnt IS NULL OR p.cnt < 10)
    ORDER BY c.ticker
""").fetchall()

if not missing:
    print("All tickers have price data. Nothing to do.")
else:
    print(f"Downloading full history for {len(missing)} tickers...\n")
    for permno, ticker in missing:
        try:
            tk = yf.Ticker(ticker)
            hist = tk.history(period="max", interval="1wk", auto_adjust=False)
            if hist.empty:
                print(f"  {ticker}: no data from yfinance")
                continue

            if isinstance(hist.columns, pd.MultiIndex):
                hist.columns = hist.columns.get_level_values(0)
            hist.columns = [c.lower().replace(" ", "_") for c in hist.columns]
            hist.index = pd.to_datetime(hist.index).normalize()

            inserted = 0
            for idx, row in hist.iterrows():
                price_date = idx.date() if hasattr(idx, "date") else idx
                con.execute("""
                    INSERT OR REPLACE INTO prices
                        (permno, price_date, open, high, low, close, adj_close, volume, split_factor)
                    VALUES (?,?,?,?,?,?,?,?,1.0)
                """, [permno, price_date,
                      row.get("open"), row.get("high"), row.get("low"),
                      row.get("close"), row.get("adj_close"),
                      int(row.get("volume") or 0)])
                inserted += 1

            refresh_momentum(permno)
            print(f"  {ticker}: {inserted} weeks loaded, momentum updated")

        except Exception as e:
            print(f"  {ticker}: ERROR — {e}")

print("\nDone. You can restart app.py now.")
