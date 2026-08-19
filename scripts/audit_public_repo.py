#!/usr/bin/env python3
"""Fail closed when private data or unsafe artifacts enter public Git history.

The normal mode audits the index and every commit reachable from local refs.
The pre-push mode reads Git's update records from stdin and audits the exact
objects that would be transferred, including detached commits and tags.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
MAX_BLOB_BYTES = 2_000_000
ZERO_OIDS = {"0" * 40, "0" * 64}
REQUIRED = {
    ".env.example",
    "CONTRIBUTING.md",
    "LICENSE",
    "README.md",
    "SECURITY.md",
    "THIRD_PARTY_NOTICES.md",
}

# These categories are intentionally generic. Project-specific fingerprints
# belong in an untracked denylist supplied with --denylist-file or configured
# as alphamaxx.privateDenylist; publishing them here would disclose them.
INTERNAL_NAMES = {
    "AGENTS.md",
    "CLAUDE.md",
    "MASTER_PRD.md",
    "PROJECT_STATE.md",
}
PRIVATE_TOP_LEVEL_DIRS = {
    ".agents",
    ".codex",
    ".direnv",
    "backups",
    "data",
    "exports",
    "imports",
    "local-data",
    "private",
    "screenshots",
    "uploads",
}
DATA_OR_ARCHIVE_SUFFIXES = {
    ".7z", ".arrow", ".avro", ".bz2", ".csv", ".dta", ".feather",
    ".gz", ".joblib", ".npy", ".npz", ".orc", ".parquet", ".pickle",
    ".pkl", ".rar", ".rds", ".sas7bdat", ".tar", ".tsv", ".xz",
    ".xls", ".xlsb", ".xlsx", ".zip",
}
DOCUMENT_OR_MEDIA_SUFFIXES = {
    ".doc", ".docx", ".gif", ".heic", ".jpeg", ".jpg", ".pdf", ".png",
    ".ppt", ".pptx", ".svg", ".tif", ".tiff", ".webp",
}
DATABASE_SUFFIXES = {
    ".db", ".duckdb", ".sqlite", ".sqlite3", ".wal",
}
CREDENTIAL_NAMES = {
    ".envrc", ".netrc", ".npmrc", ".pgpass", ".pypirc", ".sesskey",
    "credentials.json", "service-account.json", "settings.local.json",
}
CREDENTIAL_SUFFIXES = {
    ".jks", ".key", ".keystore", ".p12", ".pem", ".pfx",
}

SECRET_PATTERNS = (
    re.compile(rb"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
    re.compile(rb"(?:^|[^A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}"),
    re.compile(rb"AKIA[0-9A-Z]{16}"),
    re.compile(rb"ASIA[0-9A-Z]{16}"),
    re.compile(rb"gh[opsu]_[A-Za-z0-9]{30,}"),
    re.compile(rb"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(rb"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(rb"AIza[0-9A-Za-z_-]{30,}"),
    re.compile(rb"(?i)(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?)://[^\s/:@]+:[^\s/@]+@"),
)
LOCAL_PATH_PATTERN = re.compile(
    rb"(?:^|[\s='\"(<`\[])(?:/Users/[^/\s]+|/home/[^/\s]+|"
    rb"[A-Za-z]:\\Users\\[^\\\s]+)",
    re.MULTILINE,
)
LFS_POINTER = b"version https://git-lfs.github.com/spec/v1\n"
BLOCKED_MAGIC = (
    b"PAR1", b"SQLite format 3\x00", b"PK\x03\x04", b"\x1f\x8b", b"%PDF-",
    b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff",
)
NOREPLY_EMAIL = re.compile(r"^[^@\s]+@users\.noreply\.github\.com$", re.I)


@dataclass(frozen=True)
class GitEntry:
    mode: str
    oid: str
    path: str


class AuditError(RuntimeError):
    """Raised when Git cannot provide a complete audit view."""


def _find_git() -> str:
    candidates = [
        os.environ.get("ALPHAMAXX_GIT", ""),
        "/Library/Developer/CommandLineTools/usr/bin/git",
        shutil.which("git") or "",
    ]
    attempted: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in attempted:
            continue
        attempted.add(candidate)
        try:
            subprocess.run(
                [candidate, "--version"], check=True, capture_output=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        return candidate
    raise AuditError(
        "no working Git executable found; set ALPHAMAXX_GIT to an absolute path"
    )


GIT = _find_git()


def git(*args: str, binary: bool = False, input_bytes: bytes | None = None) -> bytes | str:
    try:
        result = subprocess.run(
            [GIT, *args], cwd=ROOT, check=True, capture_output=True,
            input=input_bytes,
        )
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", "replace").strip().splitlines()
        suffix = f": {detail[-1]}" if detail else ""
        raise AuditError(f"Git could not complete {' '.join(args[:2])}{suffix}") from None
    return result.stdout if binary else result.stdout.decode("utf-8", "replace")


def git_config_values(key: str) -> list[str]:
    result = subprocess.run(
        [GIT, "config", "--path", "--get-all", key], cwd=ROOT,
        capture_output=True,
    )
    if result.returncode == 1:
        return []
    if result.returncode != 0:
        raise AuditError(f"could not read Git configuration key {key}")
    return result.stdout.decode("utf-8", "replace").splitlines()


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "surrogateescape")).hexdigest()[:12]


def display_path(path: str, show_details: bool) -> str:
    return path if show_details else f"path fingerprint {fingerprint(path)}"


def path_problem(path: str) -> str | None:
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    parts = Path(normalized).parts
    name = parts[-1] if parts else normalized
    lower_name = name.lower()
    suffix = Path(lower_name).suffix

    if name in INTERNAL_NAMES or name.endswith("_PRD.md"):
        return "internal planning document"
    if parts and parts[0].lower() in PRIVATE_TOP_LEVEL_DIRS:
        return "private local-data directory"
    if normalized == ".gitmodules":
        return "submodule configuration"
    if lower_name != ".env.example" and (
        lower_name == ".env" or lower_name.startswith(".env.")
    ):
        return "environment file"
    if lower_name in CREDENTIAL_NAMES or suffix in CREDENTIAL_SUFFIXES:
        return "credential or session file"
    if lower_name.startswith("id_") and suffix in {"", ".pub"}:
        return "identity key file"
    if suffix in DATABASE_SUFFIXES or lower_name.endswith((".db-wal", ".db-shm")):
        return "database or journal"
    if suffix in DATA_OR_ARCHIVE_SUFFIXES:
        return "dataset or archive"
    if suffix in DOCUMENT_OR_MEDIA_SUFFIXES:
        return "document or media export"
    return None


def parse_ls_tree(raw: bytes) -> list[GitEntry]:
    entries: list[GitEntry] = []
    for item in raw.split(b"\0"):
        if not item:
            continue
        meta, sep, raw_path = item.partition(b"\t")
        fields = meta.decode("ascii", "strict").split()
        if not sep or len(fields) != 3:
            raise AuditError("unexpected ls-tree output")
        mode, _obj_type, oid = fields
        entries.append(GitEntry(mode, oid, raw_path.decode("utf-8", "surrogateescape")))
    return entries


def tree_entries(commit: str) -> list[GitEntry]:
    return parse_ls_tree(git("ls-tree", "-rz", "--full-tree", commit, binary=True))


def index_entries() -> list[GitEntry]:
    entries: list[GitEntry] = []
    raw = git("ls-files", "-sz", binary=True)
    for item in raw.split(b"\0"):
        if not item:
            continue
        meta, sep, raw_path = item.partition(b"\t")
        fields = meta.decode("ascii", "strict").split()
        if not sep or len(fields) != 3:
            raise AuditError("unexpected ls-files output")
        mode, oid, stage = fields
        if stage != "0":
            raise AuditError("the index has unresolved merge entries")
        entries.append(GitEntry(mode, oid, raw_path.decode("utf-8", "surrogateescape")))
    return entries


def load_denylist(cli_paths: list[str]) -> tuple[bytes, ...]:
    paths = [*git_config_values("alphamaxx.privateDenylist"), *cli_paths]
    values: list[bytes] = []
    for raw_path in paths:
        path = Path(raw_path).expanduser()
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            raise AuditError("a configured private denylist is unavailable or unreadable") from None
        for line in lines:
            value = line.strip()
            if value and not value.startswith("#"):
                values.append(value.encode("utf-8"))
    return tuple(dict.fromkeys(values))


def inspect_payload(
    body: bytes,
    label: str,
    errors: list[str],
    denylist: tuple[bytes, ...],
) -> None:
    for pattern in SECRET_PATTERNS:
        if pattern.search(body):
            errors.append(f"credential-shaped content in {label}")
            break
    if LOCAL_PATH_PATTERN.search(body):
        errors.append(f"absolute workstation path in {label}")
    lowered = body.lower()
    if any(value.lower() in lowered for value in denylist):
        errors.append(f"private denylist match in {label}")


def inspect_blob(
    entry: GitEntry,
    errors: list[str],
    denylist: tuple[bytes, ...],
    show_details: bool,
) -> None:
    label = display_path(entry.path, show_details)
    problem = path_problem(entry.path)
    if problem:
        errors.append(f"{problem}: {label}")
    if entry.mode == "160000":
        errors.append(f"Git submodule is not permitted: {label}")
        return
    if entry.mode == "120000":
        errors.append(f"symbolic link is not permitted: {label}")
        return
    if entry.mode not in {"100644", "100755"}:
        errors.append(f"unsupported Git file mode {entry.mode}: {label}")
        return

    try:
        size = int(git("cat-file", "-s", entry.oid).strip())
    except ValueError:
        raise AuditError("Git returned a non-numeric blob size") from None
    if size > MAX_BLOB_BYTES:
        errors.append(f"blob exceeds {MAX_BLOB_BYTES} bytes: {label}")
        return
    body = git("cat-file", "-p", entry.oid, binary=True)
    if body.startswith(LFS_POINTER):
        errors.append(f"Git LFS pointer is not permitted: {label}")
    if any(body.startswith(magic) for magic in BLOCKED_MAGIC):
        errors.append(f"blocked binary file signature: {label}")
    if b"\0" in body:
        errors.append(f"binary blob is not permitted: {label}")
    inspect_payload(body, label, errors, denylist)


def approved_emails(cli_values: list[str]) -> set[str]:
    configured = subprocess.run(
        [GIT, "config", "--get-all", "alphamaxx.allowedCommitEmail"],
        cwd=ROOT, capture_output=True,
    )
    if configured.returncode not in {0, 1}:
        raise AuditError("could not read approved commit-email configuration")
    values = configured.stdout.decode("utf-8", "replace").splitlines()
    return {value.strip().lower() for value in [*values, *cli_values] if value.strip()}


def inspect_commit(
    oid: str,
    errors: list[str],
    denylist: tuple[bytes, ...],
    allowed_emails: set[str],
) -> None:
    body = git("cat-file", "-p", oid, binary=True)
    header, _, message = body.partition(b"\n\n")
    inspect_payload(message, "commit message", errors, denylist)
    inspect_identity(header, (b"author ", b"committer "), errors, allowed_emails)


def inspect_identity(
    header: bytes,
    prefixes: tuple[bytes, ...],
    errors: list[str],
    allowed_emails: set[str],
) -> None:
    for line in header.splitlines():
        if not line.startswith(prefixes):
            continue
        match = re.search(rb"<([^<>\s]+@[^<>\s]+)>", line)
        email = match.group(1).decode("utf-8", "replace").lower() if match else ""
        if not email or (email not in allowed_emails and not NOREPLY_EMAIL.fullmatch(email)):
            errors.append("Git identity metadata contains an unapproved email address")


def inspect_tag(
    oid: str,
    errors: list[str],
    denylist: tuple[bytes, ...],
    allowed_emails: set[str],
) -> None:
    body = git("cat-file", "-p", oid, binary=True)
    header, _, message = body.partition(b"\n\n")
    inspect_payload(message, "annotated tag message", errors, denylist)
    inspect_identity(header, (b"tagger ",), errors, allowed_emails)


def resolve_tip_commit(revision: str) -> str:
    return git("rev-parse", "--verify", f"{revision}^{{commit}}").strip()


def collect_revisions(pre_push: bool) -> tuple[list[str], list[str]]:
    if pre_push:
        revisions: list[str] = []
        tips: list[str] = []
        for line in sys.stdin:
            fields = line.split()
            if len(fields) != 4:
                raise AuditError("malformed pre-push update record")
            local_ref, local_oid, _remote_ref, _remote_oid = fields
            if local_oid in ZERO_OIDS:
                continue
            if not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", local_oid):
                raise AuditError("pre-push update contains an invalid object id")
            commit = resolve_tip_commit(local_oid)
            revisions.append(local_oid)
            tips.append(commit)
            if not (local_ref.startswith("refs/heads/") or local_ref.startswith("refs/tags/")):
                raise AuditError("pre-push update uses an unsupported ref namespace")
        return list(dict.fromkeys(revisions)), list(dict.fromkeys(tips))

    refs = git("for-each-ref", "--format=%(objectname)").splitlines()
    revisions = list(dict.fromkeys([*refs, "HEAD"]))
    return revisions, [resolve_tip_commit("HEAD")]


def inspect_history(
    revisions: list[str],
    errors: list[str],
    denylist: tuple[bytes, ...],
    allowed_emails: set[str],
    show_details: bool,
) -> None:
    if not revisions:
        return
    commits = git("rev-list", *revisions).splitlines()
    seen_entries: set[tuple[str, str, str]] = set()
    for commit in commits:
        inspect_commit(commit, errors, denylist, allowed_emails)
        for entry in tree_entries(commit):
            key = (entry.mode, entry.oid, entry.path)
            if key in seen_entries:
                continue
            seen_entries.add(key)
            inspect_blob(entry, errors, denylist, show_details)

    # Annotated tag messages are not part of the commit walk.
    for revision in revisions:
        if git("cat-file", "-t", revision).strip() == "tag":
            inspect_tag(revision, errors, denylist, allowed_emails)


def inspect_required(tips: list[str], errors: list[str]) -> None:
    for tip in tips:
        tracked = {entry.path for entry in tree_entries(tip)}
        for required in sorted(REQUIRED - tracked):
            errors.append(f"required public document is missing: {required}")


def inspect_launch_topology(errors: list[str]) -> None:
    try:
        count = int(git("rev-list", "--count", "HEAD").strip())
    except ValueError:
        raise AuditError("Git returned a non-numeric commit count") from None
    if count != 1:
        errors.append(f"public launch history must contain one commit, found {count}")

    allowed = {
        "refs/heads/main",
        "refs/remotes/origin/HEAD",
        "refs/remotes/origin/main",
    }
    for ref in git("for-each-ref", "--format=%(refname)").splitlines():
        if ref not in allowed:
            errors.append(f"unexpected ref in launch repository: {ref}")


def fail(errors: list[str]) -> None:
    unique = list(dict.fromkeys(errors))
    if not unique:
        return
    print("Public-repository audit failed:", file=sys.stderr)
    for error in unique:
        print(f"  - {error}", file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--launch", action="store_true",
        help="also require the initial one-commit, main-only launch topology",
    )
    parser.add_argument(
        "--pre-push", action="store_true",
        help="read pre-push update records from stdin and audit their exact objects",
    )
    parser.add_argument(
        "--denylist-file", action="append", default=[], metavar="PATH",
        help="untracked file containing one private literal per line",
    )
    parser.add_argument(
        "--allowed-email", action="append", default=[], metavar="EMAIL",
        help="additional approved commit author/committer email",
    )
    parser.add_argument(
        "--show-sensitive-details", action="store_true",
        help="show blocked paths locally instead of privacy-preserving fingerprints",
    )
    args = parser.parse_args()

    try:
        denylist = load_denylist(args.denylist_file)
        emails = approved_emails(args.allowed_email)
        revisions, tips = collect_revisions(args.pre_push)
        errors: list[str] = []

        inspect_required(tips, errors)
        inspect_history(
            revisions, errors, denylist, emails, args.show_sensitive_details,
        )
        if not args.pre_push:
            # Catch staged content before it becomes history as well.
            for entry in index_entries():
                inspect_blob(entry, errors, denylist, args.show_sensitive_details)
        if args.launch:
            inspect_launch_topology(errors)
        fail(errors)
    except AuditError as exc:
        print(f"Public-repository audit failed closed: {exc}", file=sys.stderr)
        raise SystemExit(2) from None

    suffix = " (launch topology verified)" if args.launch else ""
    print(f"Public-repository audit passed{suffix}.")


if __name__ == "__main__":
    main()
