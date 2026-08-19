# AlphaMaxx

AlphaMaxx is a single-user, local-first financial research terminal built with
FastHTML and DuckDB. It provides company fundamentals, price charts, economic
calendar views, transcripts, standard momentum metrics, and a manual DCF
scenario calculator at `http://127.0.0.1:8000`.

This public edition contains source code and synthetic test fixtures only. It
does not contain databases, WRDS-derived observations, credentials, account
exports, real identifier corrections, private portfolio data, proprietary
signals, or investment ratings and recommendations.

## Requirements

- Python 3.11, 3.12, or 3.13
- A WRDS subscription and the relevant dataset entitlements for WRDS ingestion
- PostgreSQL/libpq credential configuration supported by the WRDS client

## Install and run

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
python -m alphamaxx
```

Set `WRDS_USERNAME` in `.env` and configure `~/.pgpass` or `PGPASSFILE` using
your institution's WRDS instructions. Do not put a database, export, API key,
password, or account identifier in the repository.

On first launch, AlphaMaxx creates its database and session key in a private
per-user state directory rather than in the checkout:

- macOS: `~/Library/Application Support/AlphaMaxx`
- Linux: `$XDG_DATA_HOME/alphamaxx` or `~/.local/share/alphamaxx`
- Windows: `%LOCALAPPDATA%\AlphaMaxx`

You can set `ALPHAMAXX_STATE_DIR` to another private location outside every Git
repository. On POSIX systems AlphaMaxx tightens the state directory to mode
`0700` and local secrets/databases to `0600` when it opens them.

The first screen is intentionally empty. Open **Data Queue**, add a ticker, and
run the queued download while the app is running. The equivalent stopped-app
command is:

```bash
python -m alphamaxx.services.ingestion --queued
```

The manual DCF tool uses only assumptions entered by the user. Its lower,
base, and upper outputs are illustrative scenarios—not probabilities,
recommendations, or advice.

## Optional AI providers

The default requirements install the Gemini integration. Other providers are
explicit optional extras:

```bash
python -m pip install ".[anthropic]"
python -m pip install ".[openai]"
```

AI features remain inactive without a configured key. When activated, the
prompt and associated economic-event context are sent to the selected provider.

## Test and privacy audit

```bash
python -m pip install -r requirements-dev.txt
pytest
python scripts/audit_public_repo.py
```

The repository includes a pre-push hook that audits the exact commits being
pushed, including deleted historical files, commit messages, binary signatures,
large files, symlinks, submodules, LFS pointers, and personal commit email
addresses. Enable the tracked hook in each clone:

```bash
git config core.hooksPath .githooks
```

Maintainers can add a private exact-value denylist without committing it:

```bash
git config alphamaxx.privateDenylist /absolute/path/to/private-denylist.txt
```

Client-side hooks are a safety layer, not a substitute for review and CI. The
scanner intentionally rejects data-like and credential-like files; use
synthetic text fixtures for tests.

## Data and network boundaries

No WRDS or other licensed vendor data is distributed here. Users must obtain
authorization and comply with WRDS, CRSP, Compustat, and any other applicable
terms. The MIT license covers AlphaMaxx code only and grants no data rights.

Depending on the feature used, AlphaMaxx may contact WRDS, Yahoo Finance,
Financial Modeling Prep, public economic-calendar sources, or a configured AI
provider. Review each provider's terms and data practices before enabling it.
Local PERMNO-to-GVKEY corrections, when needed, belong in the untracked
`gvkey_overrides.json` inside the private state directory.

## Security model

AlphaMaxx has no user authentication and binds to loopback by default. It also
checks allowed Host values, rejects cross-site browser requests, uses strict
same-site sessions, and sends restrictive browser security headers. Those
controls do not make it a multi-user or internet-facing service. Do not bind to
`0.0.0.0`, expose it to a LAN, or publish it through a tunnel or reverse proxy
without authentication, authorization, TLS, and a deployment-specific review.
See [SECURITY.md](SECURITY.md).

## Disclaimer and licenses

This software is for research and education. It does not provide investment,
legal, tax, or financial advice. Verify every calculation and source record.

Project code is licensed under the MIT License. Vendored browser assets retain
their upstream licenses; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
