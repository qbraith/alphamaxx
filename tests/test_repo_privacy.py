"""Executable regression tests for the public-history privacy boundary."""

from __future__ import annotations

import os
import hashlib
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
SCANNER = ROOT / "scripts" / "audit_public_repo.py"
REQUIRED = (
    ".env.example",
    "CONTRIBUTING.md",
    "LICENSE",
    "README.md",
    "SECURITY.md",
    "THIRD_PARTY_NOTICES.md",
)


def _git_binary() -> str:
    candidates = (
        os.environ.get("ALPHAMAXX_GIT"),
        "/Library/Developer/CommandLineTools/usr/bin/git",
        shutil.which("git"),
    )
    for candidate in candidates:
        if not candidate:
            continue
        try:
            result = subprocess.run(
                [candidate, "--version"], capture_output=True, check=False,
            )
        except OSError:
            continue
        if result.returncode == 0:
            return candidate
    pytest.fail("A working Git executable is required for privacy tests")


GIT = _git_binary()


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        [GIT, *args], cwd=repo, text=True,
    ).strip()


def _write(path: Path, body: str = "synthetic fixture\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _commit_all(repo: Path, message: str) -> str:
    _git(repo, "add", "--all")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _new_repo(tmp_path: Path, *, email: str = "tester@users.noreply.github.com") -> Path:
    repo = tmp_path / "public-fixture"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Synthetic Tester")
    _git(repo, "config", "user.email", email)
    for name in REQUIRED:
        _write(repo / name)
    scripts = repo / "scripts"
    scripts.mkdir()
    shutil.copy2(SCANNER, scripts / SCANNER.name)
    _commit_all(repo, "Initial public snapshot")
    return repo


def _audit(repo: Path, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["ALPHAMAXX_GIT"] = GIT
    return subprocess.run(
        [sys.executable, "scripts/audit_public_repo.py", *args],
        cwd=repo,
        env=env,
        input=stdin,
        text=True,
        capture_output=True,
        check=False,
    )


def test_current_repository_passes_all_history_audit():
    result = _audit(ROOT)
    assert result.returncode == 0, result.stderr


def test_security_dependency_floors_and_dev_install_are_declared():
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = set(metadata["project"]["dependencies"])
    assert {
        "starlette>=1.3.1,<2",
        "python-multipart>=0.0.31,<1",
        "click>=8.3.3,<9",
        "urllib3>=2.7,<3",
        "idna>=3.18,<4",
        "cryptography>=50,<51",
        "pyasn1>=0.6.4,<1",
        "soupsieve>=2.8.4,<3",
    } <= dependencies
    assert metadata["build-system"]["requires"][0].startswith("setuptools>=83")
    assert "pytest>=9.0.3,<10" in set(
        metadata["project"]["optional-dependencies"]["dev"]
    )
    assert (ROOT / "requirements-dev.txt").read_text(encoding="utf-8").strip() == (
        "-e .[gemini,dev]"
    )


def test_pre_push_scans_exact_detached_commit(tmp_path):
    repo = _new_repo(tmp_path)
    _git(repo, "switch", "-c", "unsafe-candidate")
    _write(repo / ".env", "WRDS_USERNAME=private-example\n")
    unsafe_oid = _commit_all(repo, "Temporary local settings")
    _git(repo, "switch", "main")

    update = (
        f"refs/heads/unsafe-candidate {unsafe_oid} refs/heads/main "
        f"{'0' * 40}\n"
    )
    result = _audit(repo, "--pre-push", stdin=update)
    assert result.returncode == 1
    assert "environment file" in result.stderr


def test_deleted_dataset_remains_blocked_by_history_scan(tmp_path):
    repo = _new_repo(tmp_path)
    _write(repo / "sample.csv", "symbol,value\nDEMO,1\n")
    _commit_all(repo, "Add temporary sample")
    (repo / "sample.csv").unlink()
    _commit_all(repo, "Remove temporary sample")

    result = _audit(repo)
    assert result.returncode == 1
    assert "dataset or archive" in result.stderr


def test_external_private_denylist_is_enforced_without_disclosing_value(tmp_path):
    repo = _new_repo(tmp_path)
    marker = "local-only-synthetic-marker"
    _write(repo / "notes.txt", f"Do not publish {marker}.\n")
    _commit_all(repo, "Add notes")
    denylist = tmp_path / "private-denylist.txt"
    _write(denylist, marker + "\n")

    result = _audit(repo, "--denylist-file", str(denylist))
    assert result.returncode == 1
    assert "private denylist match" in result.stderr
    assert marker not in result.stderr


def test_lfs_pointer_is_rejected(tmp_path):
    repo = _new_repo(tmp_path)
    _write(
        repo / "large.txt",
        "version https://git-lfs.github.com/spec/v1\n"
        "oid sha256:" + "a" * 64 + "\nsize 123\n",
    )
    _commit_all(repo, "Add large-file pointer")
    result = _audit(repo)
    assert result.returncode == 1
    assert "Git LFS pointer" in result.stderr


def test_personal_commit_email_is_rejected(tmp_path):
    repo = _new_repo(tmp_path, email="developer@example.test")
    result = _audit(repo)
    assert result.returncode == 1
    assert "unapproved email" in result.stderr


def test_personal_annotated_tag_email_is_rejected(tmp_path):
    repo = _new_repo(tmp_path)
    _git(
        repo, "-c", "user.name=Synthetic Tagger",
        "-c", "user.email=developer@example.test",
        "tag", "-a", "synthetic-release", "-m", "Synthetic release",
    )
    result = _audit(repo)
    assert result.returncode == 1
    assert "unapproved email" in result.stderr


def test_workstation_path_wrapped_in_markdown_is_rejected(tmp_path):
    repo = _new_repo(tmp_path)
    private_path = "/" + "Users/synthetic-user/private-file"
    _write(repo / "notes.txt", f"Do not publish ({private_path}).\n")
    _commit_all(repo, "Add local notes")
    result = _audit(repo)
    assert result.returncode == 1
    assert "absolute workstation path" in result.stderr


def test_private_artifact_examples_are_gitignored():
    examples = (
        "alphamaxx.db",
        "cache.parquet",
        "private/local.json",
        "imports/sample.csv",
        "exports/report.xlsx",
        "screenshots/local.png",
        ".envrc",
        "credentials.json",
    )
    for relative in examples:
        result = subprocess.run(
            [GIT, "check-ignore", "--no-index", "-q", relative],
            cwd=ROOT,
            check=False,
        )
        assert result.returncode == 0, relative


def test_scanner_contains_no_project_specific_fingerprint_tables():
    source = SCANNER.read_text(encoding="utf-8")
    assert "PRIVATE_LITERALS" not in source
    assert "EXCLUDED_SYMBOLS" not in source


def test_vendored_browser_assets_match_reviewed_hashes():
    expected = {
        "chart.umd.min.js": "48444a82d4edcb5bec0f1965faacdde18d9c17db3063d042abada2f705c9f54a",  # pragma: allowlist secret
        "htmx.min.js": "71ea67185bfa8c98c39d31717c6fce5d852370fcdfd129db4543774d3145c0de",  # pragma: allowlist secret
        "fasthtml.js": "fe3ee14d9b2b3b16171ed6192323e143229a1fb228c6b4c81cced82a40485b01",  # pragma: allowlist secret
        "surreal.js": "3004aaedc8924c1090e75d72e2617e7cbffea7b43d595b112629d10fb2f2e253",  # pragma: allowlist secret
        "css-scope-inline.js": "6284948097093c3c3df1630d64cc6e309c9386ebcad6e8039c5d09f82c5656a6",  # pragma: allowlist secret
    }
    vendor = ROOT / "static" / "vendor"
    for filename, digest in expected.items():
        assert hashlib.sha256((vendor / filename).read_bytes()).hexdigest() == digest


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission modes")
def test_local_state_and_database_are_user_private(db):
    import stat

    from alphamaxx.config import settings

    assert stat.S_IMODE(settings.STATE_DIR.stat().st_mode) == 0o700
    assert stat.S_IMODE(settings.DB_PATH.stat().st_mode) == 0o600
