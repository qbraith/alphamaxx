# Security Policy

## Supported version

Security fixes are applied to the current `main` branch.

## Reporting

Do not open a public issue containing credentials, licensed data, account
exports, private identifiers, or working exploit details. Use GitHub's private
vulnerability-reporting feature when available, or contact the repository owner
privately. Revoke or rotate an exposed credential before reporting it.

## Operating model

AlphaMaxx is a single-user local application with no authentication or
authorization layer. Keep the default `127.0.0.1` bind address. Allowed-Host,
same-origin request, same-site session, CSP, frame, and content-type protections
reduce browser-origin attacks; they are not a safe replacement for access
control on a LAN or public deployment.

Keep the state directory, database, `.env`, `.pgpass`, and API keys readable
only by your user. Never commit or attach database/WAL files, Parquet files,
licensed-data extracts, credential files, brokerage exports, private portfolio
records, or screenshots that contain private data. Use a least-privilege WRDS
account and keep state outside all Git repositories.

If you intentionally expose the server beyond loopback, you must separately add
and review authentication, authorization, TLS, proxy trust boundaries, CSRF
defenses for the deployed origin, rate limiting, audit logging, and secure secret
storage. Set `ALPHAMAXX_ALLOWED_HOSTS` to the exact deployed hostnames; do not use
a wildcard.

Optional market-data and AI features make outbound requests. Do not enable a
provider unless sending its documented request context is acceptable under your
data, institutional, and vendor-license policies.

## Repository safeguards

Run `python scripts/audit_public_repo.py` before publication and enable
`.githooks/pre-push` in every clone with `git config core.hooksPath .githooks`.
CI repeats the repository audit, but neither CI nor hooks can prove that a
licensed value was independently typed into otherwise ordinary source text.
Maintainers should therefore use an untracked exact-value denylist and complete
a human review before changing repository visibility.
