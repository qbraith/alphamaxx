"""
seed_nasdaq500.py — Seed the top 500 NASDAQ companies by market cap into alphamaxx.db

Uses a single WRDS connection (reads ~/.pgpass — no interactive prompts).
Run this once before your first full ingestion.

Usage:
    python3 scripts/seed_nasdaq500.py
"""

import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import wrds

from alphamaxx.config import settings
from alphamaxx.data import init_db
from alphamaxx.data.db import write_section
from alphamaxx.services.ingestion import _wrds_username


NASDAQ_TOP_SQL = """
WITH latest_price AS (
    SELECT
        permno,
        ABS(prc) * shrout AS mktcap_thousands,
        ROW_NUMBER() OVER (PARTITION BY permno ORDER BY date DESC) AS rn
    FROM crsp.msf
    WHERE date >= (
        SELECT MAX(date) - INTERVAL '18 months' FROM crsp.msf
    )
      AND prc  IS NOT NULL
      AND shrout IS NOT NULL
      AND shrout > 0
),
current_name AS (
    SELECT
        permno,
        ticker,
        comnam,
        exchcd,
        siccd,
        ROW_NUMBER() OVER (
            PARTITION BY permno
            ORDER BY nameendt DESC NULLS FIRST, namedt DESC
        ) AS rn
    FROM crsp.msenames
    WHERE exchcd = 3        -- NASDAQ
      AND ticker IS NOT NULL
)
SELECT
    cn.permno,
    cn.ticker,
    cn.comnam   AS name,
    lp.mktcap_thousands * 1000 AS market_cap_usd
FROM current_name cn
JOIN latest_price lp
    ON cn.permno = lp.permno AND lp.rn = 1
WHERE cn.rn = 1
  AND lp.mktcap_thousands IS NOT NULL
  AND lp.mktcap_thousands > 0
ORDER BY lp.mktcap_thousands DESC
LIMIT %(limit)s
"""


def seed(limit: int = 500) -> None:
    if not 1 <= int(limit) <= 5000:
        raise ValueError("limit must be between 1 and 5000")
    limit = int(limit)
    try:
        init_db()
    except Exception as exc:
        print(f"[error] Cannot initialize {settings.DB_PATH} ({exc}).")
        print("[error] Stop another AlphaMaxx process that is using this database.")
        raise SystemExit(1) from None
    print("[seed] Connecting to WRDS...")
    try:
        db = wrds.Connection(wrds_username=_wrds_username())
    except Exception as e:
        print(f"[error] Could not connect to WRDS: {e}")
        print("  → Ensure ~/.pgpass has an entry for wrds-pgdata.wharton.upenn.edu")
        sys.exit(1)

    print("[seed] Querying CRSP for top NASDAQ companies by market cap...")
    try:
        df = db.raw_sql(NASDAQ_TOP_SQL, params={"limit": limit})
    except Exception as e:
        print(f"[error] WRDS query failed: {e}")
        sys.exit(1)
    finally:
        try:
            db.close()
        except Exception:
            pass  # WRDS library bug: engine may be None if connection dropped

    if df.empty:
        print("[error] Query returned no results — check WRDS access to crsp.msf")
        sys.exit(1)

    print(f"[seed] Got {len(df)} companies from WRDS. Seeding into {settings.DB_PATH}...")

    new_count = 0
    update_count = 0

    with write_section() as con:
        for _, row in df.iterrows():
            permno = int(row["permno"])
            ticker = str(row["ticker"]).strip().upper()
            name = str(row["name"]).strip().title()

            existing = con.execute(
                "SELECT 1 FROM companies WHERE permno = ?", [permno]
            ).fetchone()

            con.execute("""
                INSERT INTO companies
                    (permno, ticker, name, exchange, in_watchlist, updated_at)
                VALUES (?, ?, ?, 'NASDAQ', TRUE, now())
                ON CONFLICT (permno) DO UPDATE SET
                    ticker = excluded.ticker,
                    name = excluded.name,
                    exchange = excluded.exchange,
                    in_watchlist = TRUE,
                    updated_at = now()
            """, [permno, ticker, name])

            if existing:
                update_count += 1
            else:
                new_count += 1

    print(f"[seed] Done.")
    print(f"       New companies added : {new_count}")
    print(f"       Existing updated    : {update_count}")
    print(f"       Total in watchlist  : {new_count + update_count}")
    print()
    print("Next step — run the full ingestion:")
    print("  python3 ingestion.py")
    print()
    print("Or ingest a single ticker to test first:")
    print("  python3 ingestion.py --ticker MSFT")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=500)
    seed(parser.parse_args().limit)
