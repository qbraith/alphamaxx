"""Transitional shim — the pipeline now lives in alphamaxx/services/ingestion.py.

`python3 ingestion.py --queued|--ticker|--segments-csv` keeps working.
"""

from alphamaxx.services.ingestion import (  # noqa: F401
    COMPUSTAT_COLS,
    GVKEY_OVERRIDES,
    WRDSIngester,
    load_segments_csv,
    main,
)

if __name__ == "__main__":
    main()
