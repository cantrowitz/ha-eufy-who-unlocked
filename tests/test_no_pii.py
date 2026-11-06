"""No-PII guard for this public repo.

Before any code goes to GitHub — including the first push of this
repo — scan for personal data. Runs in CI on every push; run the
suite before pushing.

Flagged (a failure here blocks the push):
- full device serials (``T85V0K`` + the rest of the serial — the
  model prefix ``T85V0`` alone is fine, it names the model, not a
  device),
- full eufy user ids (40 hex chars — the all-zeros test sentinel
  in test_lock_shim.py is not an identifier and is allowlisted),
- e-mail addresses (except the single public commit-author address
  named in the README's "commits are authored as" line — commit
  authorship is public git metadata by design),
- Home Assistant Cloud (Nabu Casa) URLs,
- LAN IPs (10/8, 192.168/16, 172.16/12 — loopback 127.0.0.1 and
  the 0.0.0.0 bind wildcard are not identifying and are allowlisted),
- session / capture / secret files present in the tree at all.

Nicknames in the verified event table are NOT flagged: they are
the nick_names eufy itself reports for unlocks and are the entire
point of the mapping tests. The GitHub handle (codeowners, repo
URLs) is likewise the public project identity, not PII.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Files/dirs that are never scanned (generated, vendored, VCS).
SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules", ".pytest_cache"}
SKIP_SUFFIXES = {".pyc"}

# Session / capture / secret files must never exist in the tree.
FORBIDDEN_FILES = {
    ".eufy-session.json",
    ".eufy-captcha.png",
    "last-unlock.json",
    ".env",
}
FORBIDDEN_SUFFIXES = {".jsonl"}

# The one e-mail allowed: the public commit-author identity.
ALLOWED_EMAILS = {"cantrowitz@gmail.com"}

# RFC 2606 example domains are documentation placeholders, not real addresses.
ALLOWED_EMAIL_DOMAINS = {"example.com"}


def _is_test_sentinel_serial(value: str) -> bool:
    """A fake serial whose suffix is one repeated digit (000…/999…)."""
    suffix = value[len("T85V0K"):]
    return len(set(suffix)) == 1

PATTERNS = {
    "full device serial": re.compile(r"T85V0K[0-9A-Z]{6,}"),
    "e-mail address": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "Nabu Casa URL": re.compile(r"[a-z0-9]+\.ui\.nabu\.casa"),
    "LAN IP": re.compile(
        r"\b(?:10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+)\b"
    ),
    "40-hex user id": re.compile(r"\b[0-9a-f]{40}\b"),
}


def repo_files():
    """All tracked-candidate files under the repo root."""
    for path in sorted(REPO.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix in SKIP_SUFFIXES:
            continue
        yield path


def test_no_forbidden_files_in_tree():
    offenders = []
    for path in repo_files():
        if path.name in FORBIDDEN_FILES or path.suffix in FORBIDDEN_SUFFIXES:
            offenders.append(str(path.relative_to(REPO)))
    assert not offenders, f"secret/session/capture files in the tree: {offenders}"


def test_gitignore_covers_secrets():
    gitignore = (REPO / ".gitignore").read_text()
    for pattern in (
        ".eufy-session.json",
        "*.jsonl",
        ".env",
        "node_modules/",
    ):
        assert pattern in gitignore, f".gitignore is missing {pattern!r}"


def test_no_pii_patterns_in_tree():
    offenders = []
    for path in repo_files():
        text = path.read_text(errors="replace")
        for name, pattern in PATTERNS.items():
            for match in pattern.finditer(text):
                value = match.group(0)
                if name == "full device serial" and _is_test_sentinel_serial(value):
                    continue  # repeated-digit fake serial in tests
                if name == "e-mail address" and (
                    value.lower() in ALLOWED_EMAILS
                    or value.rsplit("@", 1)[-1].lower() in ALLOWED_EMAIL_DOMAINS
                ):
                    continue
                if name == "40-hex user id" and set(value) == {"0"}:
                    continue  # all-zeros test sentinel, not an identifier
                offenders.append(f"{path.relative_to(REPO)}: {name}: {value}")
    assert not offenders, "PII found:\n" + "\n".join(offenders)
