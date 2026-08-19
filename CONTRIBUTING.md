# Contributing

Contributions must use fictional companies and synthetic fixtures. They must
not contain licensed vendor data, credentials, real account information,
private identifier corrections, brokerage or portfolio records, proprietary
analytics, security-sensitive internal documentation, investment ratings, or
recommendations.

Before opening a pull request:

```bash
python -m pip install -r requirements-dev.txt
pytest
python scripts/audit_public_repo.py
```

Enable the repository hook in each clone:

```bash
git config core.hooksPath .githooks
```

Keep database access in `alphamaxx/data/`, external-service orchestration in
`alphamaxx/services/`, calculations in `alphamaxx/services/`, and HTTP routes in
`alphamaxx/web/`. Add dependencies to `pyproject.toml`, include tests for every
behavioral change, and document any new outbound network or data boundary.

Never weaken or bypass the publication scanner to land a contribution. If a
synthetic fixture is rejected, redesign the fixture or propose a narrowly
reviewed scanner change with regression tests.
