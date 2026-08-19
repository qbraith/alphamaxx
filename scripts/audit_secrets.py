#!/usr/bin/env python3
"""Run detect-secrets against tracked files and fail on any finding."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from audit_public_repo import GIT, ROOT


def main() -> None:
    env = os.environ.copy()
    env["PATH"] = str(Path(GIT).parent) + os.pathsep + env.get("PATH", "")
    result = subprocess.run(
        [
            sys.executable, "-m", "detect_secrets", "scan", "--no-verify",
            "--exclude-files", r"^static/vendor/", ".",
        ],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )
    if result.returncode != 0:
        print("Secret audit failed closed: detect-secrets could not run.", file=sys.stderr)
        raise SystemExit(2)
    try:
        payload = json.loads(result.stdout)
        findings = payload["results"]
    except (json.JSONDecodeError, KeyError, TypeError):
        print("Secret audit failed closed: invalid scanner output.", file=sys.stderr)
        raise SystemExit(2) from None
    if findings:
        print("Secret audit found potential credentials in:", file=sys.stderr)
        for path in sorted(findings):
            digest = hashlib.sha256(path.encode("utf-8")).hexdigest()[:12]
            print(f"  - path fingerprint {digest}", file=sys.stderr)
        raise SystemExit(1)
    print("Tracked-file secret audit passed.")


if __name__ == "__main__":
    main()
