"""AlphaMaxx data-ingestion pipeline.

Sources: WRDS Compustat (fundamentals) + yfinance (prices).
Auth: ~/.pgpass handles WRDS credentials silently — no code-level passwords.

Usage:
    python -m alphamaxx.services.ingestion                    # all watchlist
    python -m alphamaxx.services.ingestion --ticker MSFT NVDA # specific tickers
    python -m alphamaxx.services.ingestion --queued           # UI queue
    python -m alphamaxx.services.ingestion --segments-csv F   # manual segments

(The root-level `python3 ingestion.py` shim forwards here.)
"""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import os
import re
import sys
import threading
import warnings

import duckdb
import numpy as np
import pandas as pd
import yfinance as yf

from alphamaxx.config import ensure_private_path, settings
from alphamaxx.data import (
    attach_ingestion_queue_company,
    get_company_by_ticker,
    get_conn,
    get_queued_ingestions,
    init_db,
    mark_ingestion_queue_status,
    refresh_ingestion_status,
    refresh_ttm,
    update_company_profile,
    upsert_company_identity,
    upsert_dividends,
    upsert_segment_revenue,
    upsert_stock_splits,
    watchlist_companies,
)
from alphamaxx.data.db import write_section
from alphamaxx.data.momentum import refresh_momentum
from alphamaxx.log import configure_logging

warnings.filterwarnings("ignore", category=FutureWarning)

log = logging.getLogger(__name__)


def _wrds_username() -> str:
    """Return the configured WRDS account without exposing its password.

    WRDS accounts commonly differ from the local OS username. The WRDS client
    needs that username before libpq can select the matching ``.pgpass`` row;
    otherwise non-interactive ingestion falls into an unusable prompt.
    """
    configured = os.environ.get("WRDS_USERNAME") or os.environ.get("PGUSER")
    if configured:
        return configured

    pgpass_path = os.environ.get("PGPASSFILE") or os.path.expanduser("~/.pgpass")
    try:
        with open(pgpass_path, encoding="utf-8") as pgpass:
            for raw_line in pgpass:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(":", 4)
                if len(parts) == 5 and "wrds" in parts[0].lower() and parts[3]:
                    return parts[3]
    except OSError:
        pass
    return getpass.getuser()


# ---------------------------------------------------------------------------
# Company-name comparison (recycled-ticker guard)
# ---------------------------------------------------------------------------

# Corporate/structure noise stripped before comparing a CRSP name to a yfinance
# name. CRSP recycles ticker symbols, so an identity cross-check is the only
# reliable way to tell a stale issuer (e.g. an old trust) from the current one.
_NAME_NOISE = {
    "INC", "INCORPORATED", "CORP", "CORPORATION", "CO", "COMPANY", "COMPANIES",
    "LTD", "LIMITED", "LLC", "LP", "LLP", "PLC", "SA", "NV", "AG", "AB", "SE",
    "THE", "COM", "CL", "CLASS", "SER", "SERIES", "NEW", "ADR", "ADS", "HLDG",
    "HLDGS", "HOLDING", "HOLDINGS", "GROUP", "GRP", "TRUST", "TR", "FUND",
    "FUNDS", "INV", "INVESTMENT", "INVESTMENTS", "ETF", "REIT", "PARTNERS",
    "PARTNER", "PLC", "AND",
}


class CompanyIdentityMismatch(ValueError):
    """A stored PERMNO no longer identifies the company using its ticker."""


def _name_tokens(name: str) -> set[str]:
    """Normalize a company name to a set of significant tokens."""
    if not name:
        return set()
    cleaned = re.sub(r"[^A-Z0-9 ]", " ", str(name).upper().replace("&", " AND "))
    return {
        tok for tok in cleaned.split()
        if len(tok) >= 2 and tok not in _NAME_NOISE
    }


def _names_conflict(crsp_name: str, yf_name: str) -> bool:
    """True when two names clearly refer to different companies.

    Conservative: only a conflict when both names yield significant tokens and
    those token sets are entirely disjoint (no shared word). This catches
    recycled tickers with disjoint legal names while leaving ordinary casing
    and suffix differences alone.
    """
    a, b = _name_tokens(crsp_name), _name_tokens(yf_name)
    if not a or not b:
        return False
    return a.isdisjoint(b)


def _explicit_etf_name(name: str) -> bool:
    """Whether a legal/brand name explicitly identifies an ETF.

    CRSP often stores the series trust/registrant (for example ``E T F SERIES
    SOLUTIONS``) while Yahoo uses the consumer fund name. Those names can be
    completely disjoint without representing a recycled ticker. Keep this
    narrow: a generic ``TRUST`` alone is not enough, because recycled equity
    tickers commonly collide with historical trusts.
    """
    normalized = re.sub(r"[^A-Z0-9 ]", " ", str(name or "").upper())
    return bool(
        re.search(r"\bE\s+T\s+F\b", normalized)
        or re.search(r"\bETF\b", normalized)
        or "EXCHANGE TRADED FUND" in normalized
    )


def _split_events_from_series(splits) -> list[tuple[object, float]]:
    """Normalize yfinance split events without aligning them to weekly bars."""
    if splits is None or len(splits) == 0:
        return []
    if isinstance(splits, pd.DataFrame):
        splits = splits.squeeze("columns")
    events: list[tuple[object, float]] = []
    for idx, raw_factor in splits.items():
        try:
            factor = float(raw_factor)
        except (TypeError, ValueError):
            continue
        if not np.isfinite(factor) or factor <= 0 or factor == 1.0:
            continue
        timestamp = pd.to_datetime(idx)
        events.append((timestamp.date(), factor))
    return events


# ---------------------------------------------------------------------------
# Column map: Compustat fundq → alphamaxx fundamentals table
# ---------------------------------------------------------------------------

COMPUSTAT_COLS = [
    "gvkey", "datadate", "fyearq", "fqtr", "rdq",
    "revtq",    # Revenue
    "cogsq",    # COGS
    "oibdpq",   # EBITDA (Operating Income Before D&A)
    "oiadpq",   # EBIT  (Operating Income After D&A)
    "niq",      # Net Income
    "piq",      # Pretax Income
    "txtq",     # Income Tax Expense
    "epsfxq",   # EPS Diluted
    "oancfy",   # Operating Cash Flow (YTD)
    "capxy",    # Capital Expenditures (YTD)
    "stkcpaq",  # Stock-Based Compensation
    "seqq",     # Shareholders' Equity
    "cshoq",    # Period-end Shares Outstanding (millions)
    "cshfdq",   # Diluted EPS denominator (millions)
    "cshprq",   # Common shares fallback (millions)
    "cheq",     # Cash & Equivalents
    "dlcq",     # Short-term Debt
    "dlttq",    # Long-term Debt
]

def _load_local_gvkey_overrides() -> dict[int, str]:
    """Load local-only PERMNO→GVKEY corrections from an ignored JSON file.

    A missing file is the normal public default. A malformed existing file is
    logged because silently ignoring an intended correction is unsafe.
    """
    path = settings.STATE_DIR / "gvkey_overrides.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        log.error("Cannot read %s (%s) — GVKEY overrides are inactive.", path, exc)
        return {}

    try:
        overrides = {
            int(permno): str(gvkey)
            for permno, gvkey in payload.items()
            if str(permno).isdigit() and str(gvkey).strip()
        }
        ensure_private_path(path, directory=False)
        return overrides
    except AttributeError:
        log.error("%s is not a JSON object — GVKEY overrides are inactive.", path)
        return {}


LOCAL_GVKEY_OVERRIDES = _load_local_gvkey_overrides()


def _map_fundq_row(row: pd.Series) -> dict:
    """Convert one Compustat fundq row into our fundamentals schema dict."""
    fyear = int(row.get("fyearq") or 0)
    fqtr = int(row.get("fqtr") or 0)
    fiscal_qtr = f"{fyear}Q{fqtr}" if fyear and fqtr else None

    # Use rdq (report date of quarterly earnings) if available, else datadate
    report_date = row.get("rdq") or row.get("datadate")
    if hasattr(report_date, "date"):
        report_date = report_date.date()

    def _val(k, default=None):
        v = row.get(k)
        if pd.isna(v):
            return default
        try:
            return float(v)
        except (ValueError, TypeError):
            return default

    rev = _val("revtq")
    cogs = _val("cogsq")
    gross_profit = (rev - cogs) if (rev is not None and cogs is not None) else None

    # Use the calculated quarterly values (derived in ingest_fundamentals)
    ocf = _val("oancf_q")
    capx = _val("capx_q", 0.0)
    fcf = (ocf - capx) if ocf is not None else None

    cash = _val("cheq")
    dlc = _val("dlcq")
    dltt = _val("dlttq")
    debt = ((dlc or 0.0) + (dltt or 0.0)
            if dlc is not None or dltt is not None else None)

    common_shares = _val("cshprq")
    shares_outstanding = _val("cshoq", common_shares)
    shares_diluted = _val("cshfdq", common_shares)
    if shares_outstanding is None:
        shares_outstanding = shares_diluted
    if shares_diluted is None:
        shares_diluted = shares_outstanding

    return {
        "fiscal_qtr":     fiscal_qtr,
        "report_date":    report_date,
        "revenue":        rev,
        "cogs":           cogs,
        "gross_profit":   gross_profit,
        "ebitda":         _val("oibdpq"),
        "ebit":           _val("oiadpq"),
        "net_income":     _val("niq"),
        "pretax_income":  _val("piq"),
        "income_tax":     _val("txtq"),
        "eps_diluted":    _val("epsfxq"),
        "fcf":            fcf,
        "sbc":            _val("stkcpaq"),
        "capex":          capx,
        "shareholders_equity": _val("seqq"),
        "shares_outstanding": shares_outstanding,
        "shares_diluted": shares_diluted,
        "cash":           cash,
        "debt":           debt,
    }


# ---------------------------------------------------------------------------
# Main ingester class
# ---------------------------------------------------------------------------

class WRDSIngester:

    def __init__(self, force_replace: bool = False):
        self.db_wrds = None
        self._yf_name_cache: dict[str, str | None] = {}
        # Allow a fundamentals snapshot to shrink stored history (identity fix).
        self.force_replace = force_replace

    def connect(self) -> None:
        """Open WRDS connection. Uses ~/.pgpass silently."""
        import wrds
        log.info("Connecting to WRDS PostgreSQL...")
        # wrds 3.5.0 does not honor PGUSER when it constructs its first URI;
        # passing the username lets libpq use the existing ~/.pgpass entry
        # without falling into an interactive prompt in resumable CLI jobs.
        self.db_wrds = wrds.Connection(wrds_username=_wrds_username())
        log.info("WRDS connected.")

    def disconnect(self) -> None:
        if self.db_wrds:
            try:
                self.db_wrds.close()
            except Exception:
                pass  # WRDS library bug: engine may be None if connection dropped mid-session
            self.db_wrds = None

    def _yf_name(self, ticker: str) -> str | None:
        """yfinance's company name for a ticker, or None if it isn't served.

        Memoized; only consulted by the recycled-ticker guard, so the common
        resolution path is unaffected.
        """
        key = ticker.strip().upper()
        if key in self._yf_name_cache:
            return self._yf_name_cache[key]
        name = None
        try:
            info = yf.Ticker(key).info or {}
            name = info.get("longName") or info.get("shortName") or None
        except Exception as e:
            log.warning("yfinance name lookup failed for %s: %s", key, e)
            name = None
        self._yf_name_cache[key] = name
        return name

    def _resolve_crsp_row(self, ticker: str) -> dict | None:
        """Resolve a current US ticker, guarding against recycled symbols.

        CRSP reuses ticker strings and licensed snapshots can lag wall-clock
        time. Candidate currency is therefore measured against CRSP's own
        latest name date, not ``CURRENT_DATE``. Historical-only ticker matches
        are rejected instead of silently attaching a defunct issuer. The
        yfinance name comparison is an independent second identity check.
        """
        safe_ticker = str(ticker or "").strip().upper()
        if not safe_ticker:
            return None

        sql = """
            WITH source_vintage AS (
                SELECT MAX(nameendt) AS as_of_date
                FROM crsp.msenames
                WHERE exchcd IN (1, 2, 3)
                  AND nameendt IS NOT NULL
            )
            SELECT n.permno, n.ticker, n.comnam, n.exchcd
            FROM crsp.msenames n
            CROSS JOIN source_vintage v
            WHERE UPPER(n.ticker) = %(ticker)s
              AND n.exchcd IN (1, 2, 3)
              AND n.namedt <= v.as_of_date
              AND (
                  n.nameendt IS NULL
                  OR n.nameendt >= v.as_of_date - INTERVAL '31 days'
              )
            ORDER BY
                n.nameendt DESC NULLS FIRST,
                n.namedt DESC
            LIMIT 1
        """
        result = self.db_wrds.raw_sql(sql, params={"ticker": safe_ticker})
        if result.empty:
            return None

        row = result.iloc[0]
        crsp_name = str(row["comnam"]).strip()
        yf_name = self._yf_name(safe_ticker)
        if (
            yf_name
            and _names_conflict(crsp_name, yf_name)
            and not (_explicit_etf_name(crsp_name) or _explicit_etf_name(yf_name))
        ):
            log.warning(
                "%s: CRSP identity '%s' conflicts with yfinance '%s' — likely a "
                "recycled ticker; treating as unresolved. Add manually as a synthetic "
                "PERMNO via add_company().",
                safe_ticker, crsp_name, yf_name,
            )
            return None

        exchange_map = {1: "NYSE", 2: "AMEX", 3: "NASDAQ"}
        return {
            "permno": int(row["permno"]),
            "ticker": str(row["ticker"]).strip().upper(),
            "name": crsp_name,
            "exchange": exchange_map.get(int(row["exchcd"]), ""),
        }

    def resolve_permno_for_ticker(self, ticker: str) -> int | None:
        """Resolve a current US ticker to a CRSP PERMNO."""
        row = self._resolve_crsp_row(ticker)
        return row["permno"] if row else None

    def resolve_company_for_ticker(self, ticker: str) -> dict | None:
        """Resolve a queued ticker to a CRSP company identity."""
        return self._resolve_crsp_row(ticker)

    def validate_company_identity(self, permno: int, ticker: str) -> dict | None:
        """Reject a ticker that now resolves to a *different* CRSP PERMNO.

        New queue entries already pass through ``_resolve_crsp_row``, but
        existing local companies historically bypassed that resolver.  Because
        CRSP recycles ticker strings, trusting the stored PERMNO can join a
        current yfinance price series to an unrelated historical Compustat
        issuer.  Re-resolve before every fundamentals snapshot and raise when
        the real CRSP identity has changed.

        An *unresolvable* ticker is not a conflict and must not raise. CRSP
        legitimately returns nothing for delisted symbols (whose stored PERMNO
        is still valid, and whose prices come from the CRSP fallback) and for
        synthetic local PERMNOs >= 900000, where ``_resolve_crsp_row``
        deliberately suppresses a recycled-ticker match. Both cases return
        ``None`` so the caller can skip fundamentals but still refresh prices.
        """
        resolved = self.resolve_company_for_ticker(ticker)
        if not resolved:
            log.warning(
                "%s (PERMNO %s): no current CRSP identity — skipping the "
                "fundamentals identity check and keeping the stored PERMNO.",
                ticker, permno,
            )
            return None
        if int(permno) < 900000 and int(resolved["permno"]) != int(permno):
            raise CompanyIdentityMismatch(
                f"{ticker}: stored PERMNO {permno} conflicts with current CRSP "
                f"PERMNO {resolved['permno']} ({resolved['name']}); refusing to "
                "ingest fundamentals under the wrong issuer"
            )
        return resolved

    def ensure_company_for_ticker(self, ticker: str) -> dict | None:
        """Resolve and persist a queued ticker that is not in companies yet."""
        resolved = self.resolve_company_for_ticker(ticker)
        if not resolved:
            return None
        upsert_company_identity(
            resolved["permno"],
            resolved["ticker"],
            resolved["name"],
            resolved["exchange"],
            watchlist=False,
        )
        attach_ingestion_queue_company(
            ticker,
            resolved["permno"],
            resolved["ticker"],
            resolved["name"],
        )
        get_conn().commit()
        return resolved

    def get_gvkey_for_ticker(self, ticker: str) -> str | None:
        """
        Resolve ticker -> real CRSP PERMNO -> GVKEY.
        This salvages rows with synthetic local PERMNOs such as 900001.
        """
        real_permno = self.resolve_permno_for_ticker(ticker)
        if real_permno is None:
            return None
        return self.get_gvkey(real_permno)

    def get_gvkey(self, permno: int) -> str | None:
        """
        Resolve PERMNO → GVKEY via overrides or the CRSP/Compustat merged link table.
        """
        if permno in LOCAL_GVKEY_OVERRIDES:
            return LOCAL_GVKEY_OVERRIDES[permno]

        sql = """
            SELECT gvkey
            FROM crsp.ccmxpf_linktable
            WHERE lpermno = %(permno)s
              AND linktype IN ('LC', 'LU', 'LS')
              AND (linkenddt IS NULL OR linkenddt >= CURRENT_DATE)
            ORDER BY
                CASE linkprim WHEN 'P' THEN 0 WHEN 'C' THEN 1 ELSE 2 END,
                linkdt DESC
            LIMIT 1
        """
        result = self.db_wrds.raw_sql(sql, params={"permno": int(permno)})
        if result.empty:
            # Fallback: try without active-link filter (company may be delisted)
            sql_fallback = """
                SELECT gvkey
                FROM crsp.ccmxpf_linktable
                WHERE lpermno = %(permno)s
                  AND linktype IN ('LC', 'LU', 'LS')
                ORDER BY linkdt DESC
                LIMIT 1
            """
            result = self.db_wrds.raw_sql(
                sql_fallback, params={"permno": int(permno)},
            )

        if result.empty:
            return None
        return str(result.iloc[0]["gvkey"]).strip()

    def ingest_fundamentals(
        self,
        permno: int,
        ticker: str,
        *,
        resolved_identity: dict | None = None,
        identity_checked: bool = False,
    ) -> int:
        """
        Pull Compustat quarterly fundamentals and merge them into the table.

        A clean full-source pull is a snapshot, so replacing the prior rows is
        the only way to clear quarters left behind by an older/wrong GVKEY.
        But a *short* pull — throttling, a narrower ``fundq`` window, a partly
        broken link — is indistinguishable from a good one at this layer, and
        `alphamaxx.db` is precious. So a snapshot that would shrink the stored
        history upserts instead of replacing, unless ``--force-replace`` says
        the caller really is correcting an identity.
        Returns number of rows written.
        """
        resolved = resolved_identity
        if resolved is None and not identity_checked:
            resolved = self.validate_company_identity(permno, ticker)
        if int(permno) >= 900000:
            if not resolved:
                log.warning(
                    "%s (synthetic PERMNO %s): no CRSP identity — skipping "
                    "fundamentals; prices still refresh from yfinance/CRSP.",
                    ticker, permno,
                )
                return 0
            real_permno = int(resolved["permno"])
        else:
            real_permno = int(permno)
        gvkey = self.get_gvkey(real_permno)
        if not gvkey:
            log.warning("No CRSP/Compustat link found for PERMNO %s (%s)", permno, ticker)
            return 0

        log.info("[fundq] PERMNO %s -> GVKEY %s", permno, gvkey)

        cols = ", ".join(COMPUSTAT_COLS)
        sql = f"""
            SELECT {cols}
            FROM comp.fundq
            WHERE gvkey = %(gvkey)s
              AND indfmt = 'INDL'
              AND datafmt = 'STD'
              AND popsrc = 'D'
              AND consol = 'C'
              AND fyearq IS NOT NULL
              AND fqtr   IS NOT NULL
            ORDER BY datadate ASC
        """
        df = self.db_wrds.raw_sql(
            sql, date_cols=["datadate", "rdq"], params={"gvkey": gvkey},
        )

        if df.empty:
            log.warning("No fundq rows for GVKEY %s", gvkey)
            return 0

        # Convert YTD to Quarterly for OCF and CapEx.
        # Compustat oancfy/capxy are cumulative within the fiscal year, so the
        # quarterly value is the diff from the *immediately preceding* quarter.
        # If a quarter is missing, the diff would span two quarters (double-
        # counting), so only keep it when the previous row is exactly fqtr-1;
        # a fiscal Q1 takes the YTD value as-is, anything else becomes NaN.
        df = df.sort_values(["fyearq", "fqtr"])
        grp = df.groupby("fyearq")
        contiguous = grp["fqtr"].shift() == df["fqtr"] - 1
        for ytd_col, q_col in (("oancfy", "oancf_q"), ("capxy", "capx_q")):
            quarterly = grp[ytd_col].diff().where(contiguous)
            df[q_col] = quarterly.mask(df["fqtr"] == 1, df[ytd_col])

        values = []
        for _, row in df.iterrows():
            mapped = _map_fundq_row(row)
            if not mapped["fiscal_qtr"] or not mapped["report_date"]:
                continue
            values.append([
                permno,
                mapped["fiscal_qtr"],
                mapped["report_date"],
                mapped["revenue"],
                mapped["cogs"],
                mapped["gross_profit"],
                mapped["ebitda"],
                mapped["ebit"],
                mapped["net_income"],
                mapped["pretax_income"],
                mapped["income_tax"],
                mapped["eps_diluted"],
                mapped["fcf"],
                mapped["sbc"],
                mapped["capex"],
                mapped["shareholders_equity"],
                mapped["shares_outstanding"],
                mapped["shares_diluted"],
                mapped["cash"],
                mapped["debt"],
            ])

        if not values:
            log.warning("No usable fundq rows for GVKEY %s", gvkey)
            return 0

        columns = """
            permno, fiscal_qtr, report_date,
            revenue, cogs, gross_profit, ebitda, ebit,
            net_income, pretax_income, income_tax, eps_diluted,
            fcf, sbc, capex, shareholders_equity,
            shares_outstanding, shares_diluted, cash, debt
        """
        placeholders = "(" + ",".join("?" * 20) + ")"

        with write_section() as con:
            stored = con.execute(
                "SELECT COUNT(*) FROM fundamentals WHERE permno = ?", [permno]
            ).fetchone()[0]
            replace = self.force_replace or len(values) >= stored
            if not replace:
                log.error(
                    "%s (PERMNO %s): Compustat returned %d quarters but %d are "
                    "stored — treating this as a partial pull and upserting "
                    "instead of replacing. Re-run with --force-replace if the "
                    "shorter snapshot is genuinely correct.",
                    ticker, permno, len(values), stored,
                )

            con.execute("BEGIN TRANSACTION")
            try:
                if replace:
                    con.execute("DELETE FROM fundamentals WHERE permno = ?", [permno])
                    con.executemany(
                        f"INSERT INTO fundamentals ({columns}) VALUES {placeholders}",
                        values,
                    )
                else:
                    con.executemany(
                        f"INSERT OR REPLACE INTO fundamentals ({columns}) "
                        f"VALUES {placeholders}",
                        values,
                    )
                con.execute("COMMIT")
            except Exception:
                con.execute("ROLLBACK")
                raise

        refresh_ttm(permno)
        log.info("[ttm] TTM cache refreshed for %s", ticker)
        log.info(
            "[fundq] %s %d quarters for %s",
            "Replaced snapshot with" if replace else "Upserted",
            len(values), ticker,
        )
        return len(values)

    def ingest_prices(self, permno: int, ticker: str) -> int:
        """
        Pull max-available weekly price history from yfinance.
        Returns number of rows upserted.
        """
        log.info("[yf] Downloading price history for %s...", ticker)
        try:
            yf_ticker = yf.Ticker(ticker)
            hist = yf_ticker.history(period="max", interval="1wk", auto_adjust=False)
        except Exception as e:
            log.warning("yfinance error for %s: %s", ticker, e)
            return 0

        if hist.empty:
            log.warning("No yfinance price data for %s; trying WRDS CRSP fallback...", ticker)
            return self.ingest_prices_crsp(permno, ticker)

        # Split events use their actual dates and are persisted independently
        # from weekly bars; aligning event timestamps to a Monday bar loses
        # the common mid-week case.
        try:
            splits = yf_ticker.splits
        except Exception:
            splits = pd.Series(dtype=float)
        split_events = _split_events_from_series(splits)

        hist.index = pd.to_datetime(hist.index).normalize()
        # Flatten MultiIndex columns if present (yfinance >=0.2.x)
        if isinstance(hist.columns, pd.MultiIndex):
            hist.columns = hist.columns.get_level_values(0)
        hist.columns = [c.lower().replace(" ", "_") for c in hist.columns]

        # All network fetches are done; take the shared writer lock only for
        # the DB loop so the price scheduler can't collide on current-week bars.
        upserted = 0
        with write_section() as con:
            for idx, row in hist.iterrows():
                price_date = idx.date() if hasattr(idx, "date") else idx

                con.execute("""
                    INSERT INTO prices
                        (permno, price_date, open, high, low, close, adj_close, volume, split_factor)
                    VALUES (?,?,?,?,?,?,?,?,1.0)
                    ON CONFLICT (permno, price_date) DO UPDATE SET
                        open = excluded.open,
                        high = excluded.high,
                        low = excluded.low,
                        close = excluded.close,
                        adj_close = excluded.adj_close,
                        volume = excluded.volume
                """, [
                    permno,
                    price_date,
                    row.get("open"),
                    row.get("high"),
                    row.get("low"),
                    row.get("close"),
                    row.get("adj_close"),
                    int(row.get("volume") or 0),
                ])
                upserted += 1

        split_count = upsert_stock_splits(permno, split_events)
        if split_count:
            refresh_ttm(permno)
            log.info("[split] Persisted %d split events for %s", split_count, ticker)

        log.info("[yf] Upserted %d weekly price rows for %s", upserted, ticker)
        return upserted

    def ingest_prices_crsp(self, permno: int, ticker: str) -> int:
        """
        Pull weekly prices from WRDS CRSP when yfinance no longer serves a
        delisted/merged ticker. Stores under the local app PERMNO.
        """
        real_permno = permno
        if permno >= 900000:
            resolved = self.resolve_permno_for_ticker(ticker)
            if resolved is not None:
                real_permno = resolved

        sql = """
            SELECT date, prc, openprc, bidlo, askhi, vol, cfacpr
            FROM crsp.dsf
            WHERE permno = %(permno)s
            ORDER BY date ASC
        """
        df = self.db_wrds.raw_sql(
            sql, date_cols=["date"], params={"permno": int(real_permno)},
        )
        if df.empty:
            log.warning("No CRSP daily price rows for %s (PERMNO %s)", ticker, real_permno)
            return 0

        df = df.sort_values("date").set_index("date")
        for col in ["prc", "openprc", "bidlo", "askhi"]:
            df[col] = pd.to_numeric(df[col], errors="coerce").abs()
        df["vol"] = pd.to_numeric(df["vol"], errors="coerce").fillna(0)
        df["cfacpr"] = pd.to_numeric(df["cfacpr"], errors="coerce").replace(0, np.nan).fillna(1.0)
        df["open"] = df["openprc"].fillna(df["prc"])
        df["high"] = df["askhi"].fillna(df["prc"])
        df["low"] = df["bidlo"].fillna(df["prc"])
        df["close"] = df["prc"]
        df["adj_close"] = df["close"] / df["cfacpr"]

        weekly = df.resample("W-MON", closed="left", label="left").agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "adj_close": "last",
            "vol": "sum",
        }).dropna(subset=["close"])

        upserted = 0
        with write_section() as con:
            for idx, row in weekly.iterrows():
                con.execute("""
                    INSERT INTO prices
                        (permno, price_date, open, high, low, close, adj_close, volume, split_factor)
                    VALUES (?,?,?,?,?,?,?,?,1.0)
                    ON CONFLICT (permno, price_date) DO UPDATE SET
                        open = excluded.open,
                        high = excluded.high,
                        low = excluded.low,
                        close = excluded.close,
                        adj_close = excluded.adj_close,
                        volume = excluded.volume
                """, [
                    permno,
                    idx.date() if hasattr(idx, "date") else idx,
                    row.get("open"),
                    row.get("high"),
                    row.get("low"),
                    row.get("close"),
                    row.get("adj_close"),
                    int(row.get("vol") or 0),
                ])
                upserted += 1

        log.info("[crsp] Upserted %d weekly price rows for %s", upserted, ticker)
        return upserted

    def ingest_dividends(self, permno: int, ticker: str) -> int:
        """Pull all dividend events from yfinance into the dividends table."""
        try:
            series = yf.Ticker(ticker).dividends
        except Exception as e:
            log.warning("yfinance dividends error for %s: %s", ticker, e)
            return 0
        if series is None or len(series) == 0:
            return 0
        if isinstance(series, pd.DataFrame):  # newer yfinance returns a frame
            series = series.squeeze("columns")
        events = [
            (idx.date() if hasattr(idx, "date") else idx, float(amount))
            for idx, amount in series.items()
            if amount == amount  # skip NaN
        ]
        count = upsert_dividends(permno, events)
        log.info("[div] Upserted %d dividend events for %s", count, ticker)
        return count

    def ingest_profile(self, permno: int, ticker: str) -> None:
        """Fill company website/employees/CEO from yfinance info."""
        try:
            info = yf.Ticker(ticker).info or {}
        except Exception as e:
            log.warning("yfinance info error for %s: %s", ticker, e)
            return
        website = info.get("website") or None
        sector = info.get("sector") or None
        industry = info.get("industry") or None
        employees = info.get("fullTimeEmployees")
        employees = int(employees) if employees else None
        ceo = None
        for officer in info.get("companyOfficers") or []:
            title = (officer.get("title") or "").upper()
            if "CEO" in title or "CHIEF EXECUTIVE" in title:
                ceo = officer.get("name") or None
                break
        if website or employees or ceo or sector or industry:
            update_company_profile(permno, website, employees, ceo, sector, industry)
            log.info("[info] Profile updated for %s", ticker)

    def ingest_company(self, permno: int, ticker: str) -> None:
        """Full ingestion for one company: fundamentals → prices → dividends → momentum."""
        log.info("[ingest] %s (PERMNO %s)", ticker, permno)
        # Validate before the component-level exception handlers below. A real
        # PERMNO *conflict* must abort the whole company run; otherwise current
        # prices could still be refreshed under a historical issuer's PERMNO.
        # An unresolvable ticker only skips fundamentals (see the method).
        resolved = self.validate_company_identity(permno, ticker)
        try:
            self.ingest_fundamentals(
                permno, ticker, resolved_identity=resolved, identity_checked=True
            )
        except Exception:
            log.exception("Fundamentals failed for %s", ticker)

        try:
            self.ingest_prices(permno, ticker)
        except Exception:
            log.exception("Prices failed for %s", ticker)

        try:
            self.ingest_dividends(permno, ticker)
        except Exception:
            log.exception("Dividends failed for %s", ticker)

        try:
            self.ingest_profile(permno, ticker)
        except Exception:
            log.exception("Profile failed for %s", ticker)

        try:
            refresh_momentum(permno)
            log.info("[mom] Momentum refreshed for %s", ticker)
        except Exception:
            log.exception("Momentum failed for %s", ticker)

        try:
            refresh_ingestion_status(permno)
            get_conn().commit()
            log.info("[status] Ingestion status updated for %s", ticker)
        except Exception:
            log.warning("Could not update ingestion status for %s", ticker)

    def ingest_all_watchlist(self) -> None:
        """Ingest every company flagged in_watchlist=TRUE."""
        companies = watchlist_companies()

        if not companies:
            log.info("No companies in watchlist. Add via alphamaxx.data.add_company().")
            return

        log.info("Starting full watchlist ingest: %s", [t for _, t in companies])
        for permno, ticker in companies:
            try:
                self.ingest_company(permno, ticker)
            except CompanyIdentityMismatch as exc:
                log.error("[identity] %s", exc)

        log.info("All done.")


# ---------------------------------------------------------------------------
# Queue processing (shared by the CLI and the in-app background runner)
# ---------------------------------------------------------------------------

class _ResolutionError(ValueError):
    """A queued ticker could not be resolved to a CRSP identity."""


def _process_queue_items(ingester: WRDSIngester, queued: list[dict]) -> dict[str, int]:
    """Ingest every queued item with an already-connected ingester."""
    log.info("[queue] Processing queued downloads: %s", [q['ticker'] for q in queued])
    processed = failed = 0
    for item in queued:
        requested_ticker = item.get("requested_ticker") or item.get("ticker")
        ticker = item.get("ticker") or requested_ticker
        permno = item.get("permno")
        try:
            if permno is None:
                existing = get_company_by_ticker(ticker)
                if existing:
                    permno, ticker = existing["permno"], existing["ticker"]
                    # Existing company: write the resolved permno back onto
                    # the NULL-permno queue row so the permno-keyed status
                    # updates below (running/complete) actually match it.
                    attach_ingestion_queue_company(
                        requested_ticker, int(permno), ticker
                    )
                else:
                    resolved = ingester.ensure_company_for_ticker(requested_ticker)
                    if not resolved:
                        raise _ResolutionError(
                            f"{requested_ticker} could not be resolved in WRDS"
                        )
                    permno = resolved["permno"]
                    ticker = resolved["ticker"]
                    log.info(
                        "[queue] Resolved %s -> %s / PERMNO %s (%s)",
                        requested_ticker, ticker, permno, resolved["name"],
                    )

            mark_ingestion_queue_status(permno, "running", ticker=requested_ticker)
            ingester.ingest_company(int(permno), ticker.upper())
            mark_ingestion_queue_status(permno, "complete", ticker=requested_ticker)
            get_conn().commit()
            processed += 1
        except _ResolutionError as exc:
            mark_ingestion_queue_status(permno, "failed", str(exc), ticker=requested_ticker)
            get_conn().commit()
            failed += 1
            log.warning("[queue] %s", exc)
        except Exception:
            # The ticker may have resolved fine — don't blame WRDS resolution
            # for a download/network/DB failure.
            mark_ingestion_queue_status(
                permno, "failed",
                f"{requested_ticker} download failed — see logs",
                ticker=requested_ticker,
            )
            get_conn().commit()
            failed += 1
            log.exception("[queue] Failed %s", requested_ticker)
    return {"processed": processed, "failed": failed}


def process_queue(limit: int | None = None) -> dict[str, int]:
    """Connect to WRDS and process all queued downloads. Returns a summary."""
    queued = get_queued_ingestions(limit=limit)
    if not queued:
        log.info("[queue] No queued companies to ingest.")
        return {"processed": 0, "failed": 0}
    ingester = WRDSIngester()
    ingester.connect()
    try:
        return _process_queue_items(ingester, queued)
    finally:
        ingester.disconnect()


_queue_run_lock = threading.Lock()  # single-flight for the in-app runner


def queue_run_active() -> bool:
    """True while an in-app background queue run is in progress."""
    if _queue_run_lock.acquire(blocking=False):
        _queue_run_lock.release()
        return False
    return True


def run_queue_in_background() -> bool:
    """Start one background queue run inside the app process (DuckDB allows a
    single read-write process, so the running app must do its own ingestion —
    the CLI can't write while the server holds the database). Returns False if
    a run is already in progress."""
    if not _queue_run_lock.acquire(blocking=False):
        return False

    def _run():
        try:
            process_queue()
        except Exception:
            log.exception("[queue] Background queue run failed")
        finally:
            _queue_run_lock.release()

    threading.Thread(target=_run, daemon=True, name="ingestion-queue").start()
    return True


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def load_segments_csv(path: str) -> None:
    """
    Manual segment-revenue loader (no WRDS needed).

    CSV columns: ticker,fiscal_qtr,segment,revenue
    e.g.  GOOG,2024Q4,Google Search,48000

    This lights up the Revenue-By-Segment chart for companies whose segment
    data isn't available through the licensed WRDS Compustat feed.
    """
    import csv

    inserted = 0
    skipped = []
    with open(path, newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            ticker = (row.get("ticker") or "").strip().upper()
            qtr = (row.get("fiscal_qtr") or "").strip()
            segment = (row.get("segment") or "").strip()
            raw_rev = (row.get("revenue") or "").strip()
            if not (ticker and qtr and segment and raw_rev):
                skipped.append(row)
                continue
            company = get_company_by_ticker(ticker)
            if not company:
                skipped.append(row)
                log.warning("[segments] %s not in companies table — skipped.", ticker)
                continue
            try:
                revenue = float(raw_rev)
            except ValueError:
                skipped.append(row)
                continue
            upsert_segment_revenue(company["permno"], qtr, segment, revenue)
            inserted += 1
    log.info("[segments] Loaded %d rows from %s; skipped %d.", inserted, path, len(skipped))


def main():
    configure_logging()
    parser = argparse.ArgumentParser(description="AlphaMaxx ingestion pipeline")
    parser.add_argument(
        "--ticker", nargs="+", metavar="TICKER",
        help="One or more tickers to ingest (default: all watchlist)"
    )
    parser.add_argument(
        "--queued", action="store_true",
        help="Process companies queued from the AlphaMaxx UI"
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Maximum queued companies to process with --queued"
    )
    parser.add_argument(
        "--force-replace", action="store_true",
        help="Allow a fundamentals snapshot to shrink stored history. Needed "
             "only when correcting a wrong GVKEY/identity; without it a short "
             "Compustat pull upserts instead of deleting existing quarters."
    )
    parser.add_argument(
        "--segments-csv", metavar="PATH",
        help="Load manual segment revenue from a CSV (ticker,fiscal_qtr,segment,revenue). "
             "Runs without WRDS."
    )
    args = parser.parse_args()
    init_db()

    # Manual segment loading needs no WRDS connection — handle it first and exit.
    if args.segments_csv:
        load_segments_csv(args.segments_csv)
        return

    ingester = WRDSIngester(force_replace=args.force_replace)
    try:
        ingester.connect()
    except Exception as e:
        log.error("Could not connect to WRDS: %s", e)
        log.error("Check that ~/.pgpass has an entry for wrds-pgdata.wharton.upenn.edu")
        sys.exit(1)

    try:
        if args.queued:
            queued = get_queued_ingestions(limit=args.limit)
            if not queued:
                log.info("[queue] No queued companies to ingest.")
                return
            _process_queue_items(ingester, queued)
        elif args.ticker:
            for ticker in args.ticker:
                company = get_company_by_ticker(ticker)
                if not company:
                    resolved = ingester.resolve_company_for_ticker(ticker)
                    if resolved:
                        upsert_company_identity(
                            resolved["permno"], resolved["ticker"], resolved["name"],
                            exchange=resolved["exchange"], watchlist=True,
                        )
                        company = get_company_by_ticker(resolved["ticker"])
                    if not company:
                        log.warning("%s could not be resolved to a current CRSP identity.", ticker)
                        continue
                try:
                    ingester.ingest_company(company["permno"], ticker.upper())
                except CompanyIdentityMismatch as exc:
                    log.error("[identity] %s", exc)
        else:
            ingester.ingest_all_watchlist()
    finally:
        ingester.disconnect()


if __name__ == "__main__":
    try:
        main()
    except duckdb.IOException as e:
        sys.exit(f"Cannot open alphamaxx.db ({e}).\n"
                 "The app server is probably running — stop it first, or use "
                 "the Data Queue page's 'Run Queue Now' button instead.")
