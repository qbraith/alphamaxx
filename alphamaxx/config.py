"""Central configuration. Every tunable lives here; env vars use the
ALPHAMAXX_ prefix (e.g. ALPHAMAXX_DB overrides the database path — required
by the test suite, which points it at a temp file)."""

from __future__ import annotations

import os
import stat
import sys
import sysconfig
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _static_dir() -> Path:
    """Locate browser assets in a source checkout or an installed wheel."""
    candidates = (
        ROOT / "static",
        Path(sysconfig.get_path("data")) / "share" / "alphamaxx" / "static",
    )
    for candidate in candidates:
        if (candidate / "app.js").is_file():
            return candidate
    return candidates[0]


STATIC_DIR = _static_dir()


def _default_state_dir() -> Path:
    """Return a user-private, untracked state directory for this platform."""
    override = os.environ.get("ALPHAMAXX_STATE_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "AlphaMaxx"
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "AlphaMaxx"
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "alphamaxx"


def ensure_private_path(path: Path, *, directory: bool) -> None:
    """Create or tighten a local state path on POSIX systems."""
    if directory:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "nt" and path.exists():
        wanted = 0o700 if directory else 0o600
        current = stat.S_IMODE(path.stat().st_mode)
        if current != wanted:
            path.chmod(wanted)


def _load_dotenv() -> None:
    """Populate os.environ from a gitignored ROOT/.env (KEY=VALUE per line),
    without overriding anything already set in the real environment. No
    dependency on python-dotenv — keeps secrets (e.g. ALPHAMAXX_GEMINI_API_KEY)
    out of tracked source."""
    path = ROOT / ".env"
    if not path.exists():
        return
    ensure_private_path(path, directory=False)
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()


def _env(name: str, default: str) -> str:
    return os.environ.get(f"ALPHAMAXX_{name}", default)


@dataclass(frozen=True)
class Settings:
    # Storage
    STATE_DIR: Path
    DB_PATH: Path
    PARQUET_DIR: Path

    # TTM / chart series
    TTM_WINDOW: int = 4            # quarters per trailing-twelve-month sum
    GRID_QUARTERS: int = 80        # max quarters shown in a grid pane
    CASH_DEBT_QUARTERS: int = 100
    SEGMENT_QUARTERS: int = 20
    PE_WEEKS: int = 520            # ~10y of weekly P/E history
    PRICE_WEEKS_GRID: int = 1560   # ~30y of weekly prices in detail view
    CORRELATION_WEEKS: int = 52

    # Momentum windows — prices are WEEKLY bars, so daily-horizon indicators
    # are approximated by weekly sampling: 200 trading days ≈ 40 weekly
    # closes, 50 trading days ≈ 10 weekly closes. RSI is a 14-week Wilder RSI
    # (a daily RSI-14 cannot be derived from weekly bars).
    SMA_200D_WEEKS: int = 40
    SMA_50D_WEEKS: int = 10
    RSI_WEEKS: int = 14

    # Chart rendering
    GRID_MAX_TICKS: int = 7
    DETAIL_MAX_TICKS: int = 17
    CHART_GRID_HEIGHT: int = 180
    CHART_DETAIL_HEIGHT: int = 520
    HTMX_STAGGER_MS: int = 60

    # External-data cache TTLs (seconds)
    YF_TTL_S: int = 3600
    INDEX_TTL_S: int = 900
    EARNINGS_TTL_S: int = 3600
    ECON_TTL_S: int = 3600         # econ-calendar feed cache
    FMP_API_KEY: str = ""          # optional forward-coverage source (financialmodelingprep.com)

    # Optional AI engine for economic-calendar descriptions.
    # Empty key for the active engine => deterministic local-only behavior.
    AI_ENGINE: str = "gemini"      # gemini | anthropic | openai
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.5-flash"
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-haiku-4-5-20251001"
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"
    AI_RPM: int = 5                # AI requests/minute cap (Gemini free tier = 5)

    # Price-updater schedule (US/Eastern)
    PREMARKET_HOUR: int = 9
    PREMARKET_MINUTE: int = 15
    INTRADAY_INTERVAL_MIN: int = 15
    POSTCLOSE_HOUR: int = 16
    POSTCLOSE_MINUTE: int = 1

    # Server — bind loopback only by default: the terminal has no auth, so
    # exposing it to the LAN requires opting in via ALPHAMAXX_HOST=0.0.0.0.
    HOST: str = "127.0.0.1"
    PORT: int = 8000
    ALLOWED_HOSTS: tuple[str, ...] = ("127.0.0.1", "localhost", "::1")
    LIVE_RELOAD: bool = False
    LOG_LEVEL: str = "INFO"

    @classmethod
    def from_env(cls) -> "Settings":
        state_dir = _default_state_dir()
        allowed_hosts = tuple(
            host.strip()
            for host in _env("ALLOWED_HOSTS", "127.0.0.1,localhost,::1").split(",")
            if host.strip()
        )
        if not allowed_hosts:
            raise ValueError("ALPHAMAXX_ALLOWED_HOSTS must contain at least one host")
        return cls(
            STATE_DIR=state_dir,
            DB_PATH=Path(_env("DB", str(state_dir / "alphamaxx.db"))).expanduser(),
            PARQUET_DIR=Path(_env("PARQUET_DIR", str(state_dir / "data"))).expanduser(),
            HOST=_env("HOST", "127.0.0.1"),
            PORT=int(_env("PORT", "8000")),
            ALLOWED_HOSTS=allowed_hosts,
            LIVE_RELOAD=_env("LIVE_RELOAD", "0") not in ("0", "false", "False"),
            LOG_LEVEL=_env("LOG_LEVEL", "INFO"),
            SMA_200D_WEEKS=int(_env("SMA_200D_WEEKS", "40")),
            SMA_50D_WEEKS=int(_env("SMA_50D_WEEKS", "10")),
            RSI_WEEKS=int(_env("RSI_WEEKS", "14")),
            YF_TTL_S=int(_env("YF_TTL_S", "3600")),
            INDEX_TTL_S=int(_env("INDEX_TTL_S", "900")),
            EARNINGS_TTL_S=int(_env("EARNINGS_TTL_S", "3600")),
            PREMARKET_HOUR=int(_env("PREMARKET_HOUR", "9")),
            PREMARKET_MINUTE=int(_env("PREMARKET_MINUTE", "15")),
            INTRADAY_INTERVAL_MIN=int(_env("INTRADAY_INTERVAL_MIN", "15")),
            POSTCLOSE_HOUR=int(_env("POSTCLOSE_HOUR", "16")),
            POSTCLOSE_MINUTE=int(_env("POSTCLOSE_MINUTE", "1")),
            ECON_TTL_S=int(_env("ECON_TTL_S", "3600")),
            FMP_API_KEY=_env("FMP_API_KEY", ""),
            AI_ENGINE=_env("AI_ENGINE", "gemini"),
            GEMINI_API_KEY=_env("GEMINI_API_KEY", ""),
            GEMINI_MODEL=_env("GEMINI_MODEL", "gemini-2.5-flash"),
            ANTHROPIC_API_KEY=_env("ANTHROPIC_API_KEY", ""),
            ANTHROPIC_MODEL=_env("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"),
            OPENAI_API_KEY=_env("OPENAI_API_KEY", ""),
            OPENAI_MODEL=_env("OPENAI_MODEL", "gpt-4o-mini"),
            AI_RPM=int(_env("AI_RPM", "5")),
        )


settings = Settings.from_env()
