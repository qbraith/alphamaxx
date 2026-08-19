"""
backfill_dividends.py — Fill dividends + company profile (website/employees/CEO)
for companies ingested before those stages existed.
Run once with the app stopped: python3 scripts/backfill_dividends.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import duckdb

from alphamaxx.data import get_conn, init_db
from alphamaxx.services.ingestion import WRDSIngester

PACE_S = 0.6  # ~2 yfinance calls per company; stay well under rate limits

try:
    init_db()
    con = get_conn()
except duckdb.IOException as e:
    sys.exit(f"Cannot open alphamaxx.db ({e}).\n"
             "The app server is probably running — stop it first "
             "(DuckDB allows one writer process).")

# Companies missing dividend history or any profile fact. Re-runs are cheap:
# both stages upsert, and completed companies drop out of this query.
targets = con.execute("""
    SELECT c.permno, c.ticker
    FROM companies c
    LEFT JOIN (SELECT DISTINCT permno FROM dividends) d ON d.permno = c.permno
    WHERE c.ticker IS NOT NULL
      AND (d.permno IS NULL
           OR (c.website IS NULL AND c.employees IS NULL AND c.ceo IS NULL))
    ORDER BY c.ticker
""").fetchall()

if not targets:
    print("All companies already have dividend + profile data. Nothing to do.")
    sys.exit(0)

print(f"Backfilling dividends/profile for {len(targets)} companies "
      f"(~{len(targets) * PACE_S / 60:.0f} min)...\n")

ing = WRDSIngester()  # dividends/profile stages never touch WRDS
ok = errors = 0
for i, (permno, ticker) in enumerate(targets, 1):
    try:
        n = ing.ingest_dividends(permno, ticker)
        ing.ingest_profile(permno, ticker)
        ok += 1
        print(f"  [{i}/{len(targets)}] {ticker}: {n} dividend events")
    except Exception as e:
        errors += 1
        print(f"  [{i}/{len(targets)}] {ticker}: ERROR — {e}")
    time.sleep(PACE_S)

print(f"\nDone: {ok} companies updated, {errors} errors. You can restart the app now.")
