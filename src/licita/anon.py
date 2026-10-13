"""Anonymisation gate: the tracked tree must not name the deployment's buyers, region or tribunal.

The denylist itself would leak those names, so the repository stores only keyed hashes of normalised phrases
(`anon/denylist.sha256`): HMAC-SHA256 under a secret key that never enters the repository. Without the key the
file is just random hex; with it, CI can run the gate. The plain list stays private (`private/denylist.txt`,
gitignored); the key lives in `private/anon.key` locally and in the `LICITA_ANON_KEY` secret in CI.

  python -m licita.anon keygen                      create private/anon.key (once per deployment)
  python -m licita.anon hash private/denylist.txt   print keyed hashes for a plain list (one phrase per line)
  python -m licita.anon check                       scan every tracked file (CI runs this)

Normalisation: Unicode accents stripped, case folded, every non-alphanumeric run becomes one space.
Phrases of one to three words are matched, so "Santa Fé", "santa-fe" and "SANTA FE" all hit the same hash.
Binary data files are refused outside `demo/` (compressed Parquet/DuckDB would slip past a text scan).

Why keyed: an unkeyed SHA-256 of a one- or two-word phrase is reversible by anyone with a list of Spanish
places, hospitals or health services. The key makes the published file useless to a reader and keeps the
gate honest about what it protects. Note the oracle the test suite creates: a clean run proves the tree
contains none of the protected phrases, so a phrase that *does* appear in the tree is known not to be protected.

`anon/allowlist.txt` names the few tracked files the phrase scan skips, one path per line with the reason
after a `#`: national fact tables that must list every region to identify none (a table missing exactly
one region would point at the deployment). Allowlisted files still face the binary and size checks.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from licita.config import ROOT

DENYLIST = ROOT / "anon" / "denylist.sha256"
ALLOWLIST = ROOT / "anon" / "allowlist.txt"
KEY_FILE = ROOT / "private" / "anon.key"
KEY_ENV = "LICITA_ANON_KEY"
MAX_WORDS = 3
BINARY_EXTS = {".parquet", ".duckdb", ".pdf", ".zip", ".xlsx", ".xls", ".docx", ".pptx", ".sqlite", ".db"}
BINARY_OK_PREFIXES = ("demo/",)
MAX_BYTES = 2_000_000
SIZE_EXEMPT = {"uv.lock"}


def normalise(text: str) -> str:
    s = unicodedata.normalize("NFKD", text.casefold())
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return " ".join(re.findall(r"[a-z0-9]+", s))


def load_key(path: Path = KEY_FILE) -> bytes | None:
    """The HMAC key: `LICITA_ANON_KEY` (CI secret) or the private key file; None when neither exists."""
    env = os.environ.get(KEY_ENV, "").strip()
    if env:
        return env.encode()
    if path.is_file():
        return path.read_text().strip().encode() or None
    return None


def digest(phrase: str, key: bytes) -> str:
    return hmac.new(key, normalise(phrase).encode(), hashlib.sha256).hexdigest()


def load_hashes(path: Path = DENYLIST) -> frozenset[str]:
    if not path.is_file():
        return frozenset()
    return frozenset(line.strip() for line in path.read_text().splitlines()
                     if line.strip() and not line.startswith("#"))


def load_allowlist(path: Path = ALLOWLIST) -> frozenset[str]:
    """Tracked paths whose text is not phrase-scanned (each line: `path  # reason`)."""
    if not path.is_file():
        return frozenset()
    return frozenset(line.split("#", 1)[0].strip() for line in path.read_text().splitlines()
                     if line.split("#", 1)[0].strip())


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    detail: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.detail}"


def scan_text(text: str, hashes: frozenset[str], key: bytes, path: str = "<text>") -> list[Finding]:
    found = []
    for lineno, line in enumerate(text.splitlines(), 1):
        words = normalise(line).split()
        for n in range(1, MAX_WORDS + 1):
            for i in range(len(words) - n + 1):
                phrase = " ".join(words[i:i + n])
                if hmac.new(key, phrase.encode(), hashlib.sha256).hexdigest() in hashes:
                    found.append(Finding(path, lineno, f"denylisted phrase {phrase!r}"))
    return found


def scan_file(rel: str, hashes: frozenset[str], key: bytes, root: Path = ROOT,
              allow: frozenset[str] = frozenset()) -> list[Finding]:
    path = root / rel
    if not path.is_file():          # deleted in the working tree but still in the index
        return []
    size = path.stat().st_size
    if path.suffix.lower() in BINARY_EXTS and not rel.startswith(BINARY_OK_PREFIXES):
        return [Finding(rel, 0, f"binary data file ({path.suffix}) outside {', '.join(BINARY_OK_PREFIXES)}")]
    if size > MAX_BYTES and rel not in SIZE_EXEMPT:
        return [Finding(rel, 0, f"file is {size:,} bytes (limit {MAX_BYTES:,})")]
    if rel in allow:
        return []                   # national fact table: see anon/allowlist.txt
    data = path.read_bytes()
    if b"\0" in data[:8192]:
        return []                   # other binaries (images, fonts) carry no searchable text
    return scan_text(data.decode("utf-8", errors="replace"), hashes, key, rel)


def tracked_files(root: Path = ROOT) -> list[str]:
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                         cwd=root, check=True, capture_output=True, text=True).stdout
    return sorted(set(out.splitlines()))


def check(root: Path = ROOT, hashes: frozenset[str] | None = None, key: bytes | None = None) -> list[Finding]:
    hashes = load_hashes(root / "anon" / "denylist.sha256") if hashes is None else hashes
    key = load_key(root / "private" / "anon.key") if key is None else key
    if key is None:
        raise RuntimeError(f"no anonymity key: set {KEY_ENV} or create private/anon.key (`licita anon keygen`)")
    allow = load_allowlist(root / "anon" / "allowlist.txt")
    return [f for rel in tracked_files(root) for f in scan_file(rel, hashes, key, root, allow)]


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args[:1] == ["keygen"]:
        if KEY_FILE.exists():
            print(f"{KEY_FILE.relative_to(ROOT)} exists; delete it first to rotate (then re-hash the denylist)",
                  file=sys.stderr)
            return 1
        KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        KEY_FILE.write_text(secrets.token_hex(32) + "\n")
        KEY_FILE.chmod(0o600)
        print(f"wrote {KEY_FILE.relative_to(ROOT)}; store the same value as the {KEY_ENV} secret in CI")
        return 0
    key = load_key()
    if args[:1] == ["hash"] and len(args) == 2:
        if key is None:
            print(f"no key: set {KEY_ENV} or run `licita anon keygen`", file=sys.stderr)
            return 2
        phrases = [p for p in Path(args[1]).read_text(encoding="utf-8").splitlines()
                   if p.strip() and not p.lstrip().startswith("#")]
        bad = [p for p in phrases if len(normalise(p).split()) > MAX_WORDS]
        if bad:
            print(f"phrases longer than {MAX_WORDS} words never match; split them: {bad}", file=sys.stderr)
            return 2
        print("# HMAC-SHA256 of normalised denylisted phrases under a key kept outside the repository. "
              "Generated by `python -m licita.anon hash`.")
        print("\n".join(sorted({digest(p, key) for p in phrases})))
        return 0
    if args[:1] in ([], ["check"]):
        hashes = load_hashes()
        if not hashes:
            print(f"anon: no denylist at {DENYLIST.relative_to(ROOT)}; nothing to check against", file=sys.stderr)
            return 2
        if key is None:
            print(f"anon: no key ({KEY_ENV} or private/anon.key); the gate cannot run without it", file=sys.stderr)
            return 2
        findings = check(hashes=hashes, key=key)
        for f in findings:
            print(f)
        print(f"anon: {len(findings)} finding(s) in {len(tracked_files())} files, {len(hashes)} denylisted phrases, "
              f"{len(load_allowlist())} allowlisted file(s)", file=sys.stderr)
        return 1 if findings else 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
