#!/usr/bin/env python3
"""
scripts/release_checks.py — executable checks for the release gate (v8.0 S3-2 support)
====================================================================

Why this is its own script
--------------------
The release state machine (`gcat-std/scripts/release-gate.py`) follows the gcat-std red
line of **never using a shell** (`shlex.split` → argv list → no metacharacter
interpretation). So composite checks like "version consistency / privacy scan / service
self-reported version / changelog" can't be written as a piped shell one-liner — they
have to be an executable with a clear exit code.

Every subcommand is **independently runnable and speaks through its exit code**:
  0 = pass   1 = fail   2 = usage/environment problem
This lets the gate be automated while still letting a human re-check any single item by
hand (it's not a black box).

Subcommands
------
  version-consistency   version number consistent in three places: VERSION ↔ README badge ↔ CHANGELOG's first entry
  privacy               privacy scan: hardcoded keys/IPs/domains/local paths — criterion is "zero output"
  service-version URL   the live service's self-reported version must == the local VERSION (guards against "docs say it shipped but it didn't")
  changelog VERSION     CHANGELOG must contain an entry for this version
  artifact POINTER      artifact pointer integrity: path exists + sha256 can be computed

Usage:
  python3 scripts/release_checks.py version-consistency
  python3 scripts/release_checks.py privacy
  python3 scripts/release_checks.py service-version http://127.0.0.1:18010/
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Privacy-scan patterns (same spirit as the CI gate):
#   ⚠️ A scanner inevitably self-references — the pattern definitions themselves contain
#   sensitive-string syntax → must exclude itself
SELF_EXCLUDE = {"scripts/release_checks.py", ".github/workflows/privacy.yml",
                ".github/workflows/ci.yml"}
PRIVACY_PATTERNS = [
    ("private key block", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("plaintext key assignment", r"(?i)(api[_-]?key|secret|passwd|password|token)\s*[:=]\s*['\"][A-Za-z0-9_\-]{16,}['\"]"),
    ("common cloud key prefix", r"\b(sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{20,})"),
    ("real IPv4 (excluding 127/0/255/doc ranges)", r"\b(?!127\.|0\.|255\.|10\.0\.2\.)(?:\d{1,3}\.){3}\d{1,3}\b"),
    # Only flags **real accounts**, not placeholders like /home/u/ used in tests —
    # the criterion is "leaked this machine's identity", not "contains /home/".
    # Tightened after this pattern false-positived on the `/home/u/proj/a.py` fixture in
    # tests (a generic placeholder carrying zero information).
    ("local account path", r"/home/(g-cat|ubuntu|gy_ma)/"),
    ("Windows user path", r"[A-Za-z]:\\\\?Users\\\\?(gy_ma|g-cat|Administrator)"),
]


def _tracked_files() -> list[str]:
    """Only looks at git-tracked files — so the local run matches actual CI behavior (a
    lesson learned the hard way, now in the governance standard).

    ⚠️ Must pass `-c core.quotepath=false`: otherwise git escapes **non-ASCII filenames**
    into quoted octal strings like `"\\351\\207\\207\\224..."`, and `open()` immediately
    raises FileNotFoundError (hit this in practice with a Chinese-named ADR file).
    """
    try:
        out = subprocess.run(["git", "-c", "core.quotepath=false", "-C", ROOT, "ls-files"],
                             capture_output=True, text=True, timeout=60)
        if out.returncode == 0:
            return [f for f in out.stdout.splitlines() if f.strip()]
    except Exception:  # noqa: BLE001
        pass
    files = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in
                       (".git", ".venv", "__pycache__", ".pytest_cache", "node_modules")]
        for fn in filenames:
            files.append(os.path.relpath(os.path.join(dirpath, fn), ROOT))
    return files


def check_version_consistency() -> int:
    vpath = os.path.join(ROOT, "VERSION")
    if not os.path.exists(vpath):
        print("❌ Missing VERSION file")
        return 1
    ver = open(vpath, encoding="utf-8").read().strip()
    problems = []
    for readme in ("README.md",):
        p = os.path.join(ROOT, readme)
        if not os.path.exists(p):
            continue
        txt = open(p, encoding="utf-8").read()
        if ver not in txt:
            problems.append(f"{readme} does not contain version {ver}")
    cl = os.path.join(ROOT, "CHANGELOG.md")
    if not os.path.exists(cl):
        problems.append("Missing CHANGELOG.md")
    else:
        head = open(cl, encoding="utf-8").read()[:4000]
        if ver not in head:
            problems.append(f"{ver} not found at the top of CHANGELOG")
    if problems:
        print("❌ Version consistency failed: " + "; ".join(problems))
        return 1
    print(f"✅ Version consistency passed (VERSION={ver})")
    return 0


def check_privacy() -> int:
    hits = []
    for rel in _tracked_files():
        if rel in SELF_EXCLUDE:
            continue
        p = os.path.join(ROOT, rel)
        try:
            with open(p, encoding="utf-8", errors="ignore") as f:
                for lineno, line in enumerate(f, 1):
                    for name, pat in PRIVACY_PATTERNS:
                        if re.search(pat, line):
                            hits.append(f"{rel}:{lineno} [{name}] {line.strip()[:110]}")
        except (IsADirectoryError, PermissionError):
            continue
    if hits:
        print(f"❌ Privacy scan found {len(hits)} hit(s) (criterion: zero output):")
        for h in hits[:40]:
            print("   " + h)
        return 1
    print(f"✅ Privacy scan passed (scanned {len(_tracked_files())} tracked files, zero output)")
    return 0


def check_service_version(url: str) -> int:
    ver = open(os.path.join(ROOT, "VERSION"), encoding="utf-8").read().strip()
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            body = r.read().decode("utf-8", "ignore")
    except Exception as e:  # noqa: BLE001
        print(f"❌ Could not reach {url}: {type(e).__name__}: {e}")
        return 2
    if ver in body:
        print(f"✅ Live service's self-reported version contains {ver} ({body.strip()[:120]})")
        return 0
    print(f"❌ Live service's self-reported version doesn't match local VERSION({ver}) → {body.strip()[:160]}")
    return 1


def check_changelog(ver: str) -> int:
    cl = os.path.join(ROOT, "CHANGELOG.md")
    if not os.path.exists(cl):
        print("❌ Missing CHANGELOG.md")
        return 1
    txt = open(cl, encoding="utf-8").read()
    if ver in txt:
        print(f"✅ CHANGELOG contains {ver}")
        return 0
    print(f"❌ CHANGELOG is missing {ver}")
    return 1


def check_artifact(pointer: str) -> int:
    """Artifact pointer integrity: path exists + sha256 can be computed (memory only stores the pointer + fingerprint)."""
    p = os.path.expanduser(pointer)
    if not os.path.exists(p):
        print(f"❌ Artifact does not exist: {p}")
        return 1
    if os.path.isdir(p):
        print(f"✅ Directory exists: {p} (not hashed — use a file pointer instead of a directory)")
        return 0
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    print(f"✅ Artifact intact: {p}  sha256={h.hexdigest()[:16]}…  {os.path.getsize(p)} bytes")
    return 0


def check_plan(path: str) -> int:
    """P0 plan-stage acceptance criterion: the plan document exists and **contains an
    acceptance-criteria table and an out-of-scope list** (otherwise the plan can't be accepted).

    Why this is a command instead of "just eyeball it": the standard spells it out clearly —
    a plan's acceptance criterion is "every requirement has a judgeable criterion"; a plan
    without criteria isn't allowed to enter development.
    """
    p = os.path.join(ROOT, path) if not os.path.isabs(path) else path
    if not os.path.exists(p):
        print(f"❌ P0 plan document does not exist: {p}")
        return 1
    txt = open(p, encoding="utf-8").read()
    need = {"Acceptance criteria": "acceptance-criteria table", "Out of scope": "out-of-scope/won't-do list"}
    missing = [desc for kw, desc in need.items() if kw not in txt]
    if missing:
        print(f"❌ Plan is missing acceptance elements: {missing} (a plan may not start work without criteria)")
        return 1
    print(f"✅ P0 plan elements complete (acceptance-criteria table + out-of-scope list): {path}  {len(txt)} chars")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="v8.0 release-gate checks")
    ap.add_argument("check", choices=["version-consistency", "privacy",
                                      "service-version", "changelog", "artifact", "plan"])
    ap.add_argument("arg", nargs="?", default=None)
    a = ap.parse_args()
    if a.check == "version-consistency":
        return check_version_consistency()
    if a.check == "privacy":
        return check_privacy()
    if a.check == "service-version":
        if not a.arg:
            print("URL argument required", file=sys.stderr)
            return 2
        return check_service_version(a.arg)
    if a.check == "changelog":
        return check_changelog(a.arg or open(os.path.join(ROOT, "VERSION"),
                                             encoding="utf-8").read().strip())
    if a.check == "artifact":
        if not a.arg:
            print("Path argument required", file=sys.stderr)
            return 2
        return check_artifact(a.arg)
    if a.check == "plan":
        if not a.arg:
            print("Plan document path argument required", file=sys.stderr)
            return 2
        return check_plan(a.arg)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
