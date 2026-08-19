"""Transitional shim — the app now lives in alphamaxx/web/app.py.

`python app.py` and `uvicorn app:app` keep working; the canonical entry
point is `python -m alphamaxx`.
"""

from alphamaxx.web.app import app, rt  # noqa: F401

if __name__ == "__main__":
    from alphamaxx.__main__ import main
    main()
