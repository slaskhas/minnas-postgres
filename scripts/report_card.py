#!/usr/bin/env python3
"""
Report cards — extract "deliverable-grade reports" out of sessions, keep them as
structured records, and optionally push them into the Memory Palace

Why: the final report is the most concentrated output of each task round, but
session-level archiving mashes the whole session into one memory, so the report's
**absolute deliverable paths / URLs / hashes / measured numbers** can't be found at the
semantic-search layer.
This tool extracts those elements separately into a "report card" (the evidence layer
keeps a verbatim excerpt; the semantic layer keeps a retrievable entry).

Usage:
  python3 report_card.py --session-id ID            # extract the card for a single session (local only)
  python3 report_card.py --last 5 --push            # extract cards for the last 5 sessions and push them into the Memory Palace
  python3 report_card.py --last 1 --dry-run         # preview what would be extracted, write nothing
  python3 report_card.py --list                     # list extracted cards

Output: ~/.hermes/reports/cards.jsonl (one card per line) + .index.json (dedup index)
Push target: POST /api/v1/memories {user_id, content, category='worklog'}
      category is drawn from the capabilities controlled vocabulary (knowledge|pitfall|reference|
      project|ops|deploy|preference|session|worklog|temp) — **never invent your own**, or the
      server will silently normalize it to knowledge.
"""
import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import urllib.request
from datetime import datetime

HERMES_DB = os.path.expanduser("~/.hermes/state.db")
REPORTS_DIR = os.environ.get("REPORTS_DIR", os.path.expanduser("~/.hermes/reports"))
CARDS_FILE = os.path.join(REPORTS_DIR, "cards.jsonl")
INDEX_FILE = os.path.join(REPORTS_DIR, ".index.json")
MNEMOSYNE_MEMORIES_API = os.environ.get(
    "MNEMOSYNE_MEMORIES_API", "http://127.0.0.1:18010/api/v1/memories")

# capabilities controlled vocabulary (authoritative: GET /api/v1/capabilities) —— only
# values from this list are allowed
CONTROLLED_CATEGORIES = frozenset([
    "knowledge", "pitfall", "reference", "project", "ops",
    "deploy", "preference", "session", "worklog", "temp",
])
CATEGORY = "worklog"          # report-card category: work-output record
assert CATEGORY in CONTROLLED_CATEGORIES, "report-card category must be in the controlled vocabulary"

EXCERPT = 1500                # cap on the verbatim excerpt
MAX_SIG_LEN = 160             # cap on a single signal fragment (prevents treating a whole diff dump as a "path")
FINAL_LOOKBACK = 3            # how many messages "final" mode looks back (sessions often end on a short closing line, with the report just before it)
THRESHOLD_SCAN = 3            # "scan" mode: minimum number of distinct signal kinds (measured: >=2 is too loose — 3 sessions produced 66 cards → polluted memory)
STRICT_KINDS = ("收尾语", "哈希", "校验语", "路径")   # "scan" mode must hit at least one of these
REPORT_TAIL = "汇报完毕请指示"

# Signal definitions: deliverable elements —— an AI message containing these = a
# deliverable-grade report, not idle chat
SIGNALS = [
    # Backslash/plus/backtick are excluded: tool-output `\n` escapes and diff `+++` lines
    # were getting misdetected as paths (hit this in practice)
    ("路径", re.compile(r"[A-Za-z]:\\[^\s\"'，。；）)\]`+]+")),
    ("路径", re.compile(r"/(?:home|mnt|opt|var|etc|usr|srv|root|tmp)/[^\s\"'，。；）)\]`+\\]+")),
    ("URL", re.compile(r"https?://[^\s\"'，。；）)\]]+")),
    ("哈希", re.compile(r"\b[0-9a-f]{16,64}\b")),
    ("实测数字", re.compile(r"\b\d+(?:\.\d+)?\s?(?:GiB|MiB|KiB|GB|MB|KB|TB|G|M|K)\b")),
    ("收尾语", re.compile(re.escape(REPORT_TAIL))),
    ("校验语", re.compile(r"哈希(?:一致|比对)|sha256|校验(?:通过|一致)|已核验|实测(?:通过|验证)")),
]


def extract_signals(text: str) -> dict:
    """Extract and group by signal type (order-preserving dedup), returning {signal_name: [matched fragments...]}"""
    out = {}
    for name, rx in SIGNALS:
        hits = []
        for m in rx.finditer(text or ""):
            v = m.group(0).rstrip(".,;:、，。；）)]}>*\\+`")
            if not v or len(v) > MAX_SIG_LEN or v in hits:
                continue
            hits.append(v)
        if hits:
            out.setdefault(name, []).extend(hits)
    return out


def is_report_card(text: str, signals: dict | None = None, mode: str = "final") -> bool:
    """Determine whether this is a deliverable-grade report.

    mode="final" (default): the final report —— for each session, only look at the
                        **last** AI message; hitting any one deliverable element counts.
                        (per the user's definition: "don't you always write a report
                        after finishing something?")
    mode="scan":  scan the whole session, stricter threshold: >=3 signal kinds **and**
                        at least one of them is deliverable evidence (path/hash/
                        verification/closing phrase).
    mode="all":   loosest (>=2 kinds), analysis only — don't use this for storage.
    """
    signals = signals if signals is not None else extract_signals(text)
    kinds = [k for k, v in signals.items() if v]
    if mode == "final":
        return len(kinds) >= 1
    if mode == "all":
        return len(kinds) >= 2
    return len(kinds) >= THRESHOLD_SCAN and any(k in kinds for k in STRICT_KINDS)


def message_time(ts) -> str:
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return str(ts)


def build_card(msg: dict, signals: dict | None = None) -> dict:
    """Turn a message into a report card (a dict, directly json.dumps-able)"""
    content = (msg.get("content") or "").strip()
    signals = signals if signals is not None else extract_signals(content)
    card = {
        "session_id": msg.get("session_id", ""),
        "msg_id": msg.get("id"),
        "time": message_time(msg.get("timestamp")),
        "signals": {k: len(v) for k, v in sorted(signals.items())},
        "paths": sorted(set(signals.get("路径", [])))[:20],
        "urls": sorted(set(signals.get("URL", [])))[:10],
        "hashes": sorted(set(signals.get("哈希", [])))[:10],
        "measures": sorted(set(signals.get("实测数字", [])))[:20],
        "excerpt": content[:EXCERPT],
        "chars": len(content),
    }
    return card


def card_digest(card: dict) -> str:
    """Card fingerprint (for dedup): session + message + verbatim text, independent of time"""
    raw = f"{card['session_id']}|{card['msg_id']}|{card['excerpt']}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def card_text(card: dict) -> str:
    """The body pushed into the Memory Palace (human-readable + semantically searchable)"""
    parts = [f"[汇报卡] {card['session_id']} · msg#{card['msg_id']} · {card['time']}",
             "信号: " + ", ".join(f"{k}×{v}" for k, v in card["signals"].items())]
    if card["paths"]:
        parts.append("交付物路径: " + "; ".join(card["paths"]))
    if card["urls"]:
        parts.append("URL: " + "; ".join(card["urls"]))
    if card["hashes"]:
        parts.append("哈希: " + "; ".join(card["hashes"]))
    if card["measures"]:
        parts.append("实测数字: " + "; ".join(card["measures"]))
    parts.append("原文摘录:\n" + card["excerpt"])
    return "\n".join(parts)


def load_index() -> dict:
    try:
        with open(INDEX_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_index(idx: dict):
    os.makedirs(REPORTS_DIR, exist_ok=True)
    with open(INDEX_FILE, "w") as f:
        json.dump(idx, f, ensure_ascii=False, indent=1)


def append_cards(cards: list):
    os.makedirs(REPORTS_DIR, exist_ok=True)
    with open(CARDS_FILE, "a") as f:
        for c in cards:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")


def push_card(card: dict) -> dict:
    """Push into the Memory Palace: POST /api/v1/memories (json body + controlled category)"""
    payload = json.dumps({
        "user_id": "default",
        "content": card_text(card),
        "category": CATEGORY,
    }).encode()
    try:
        req = urllib.request.Request(
            MNEMOSYNE_MEMORIES_API, data=payload,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return {"stored": False, "error": str(e)}


def fetch_report_messages(db_path: str, session_id: str | None = None, last: int = 1,
                          mode: str = "final", max_per_session: int = 3) -> list:
    """Fetch candidate AI messages (with body text), newest session first

    mode="final": for each session, only take the last AI message that has body text (the final report).
    Other modes: scan everything; the hit threshold is decided by is_report_card, up to max_per_session cards per session.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    if session_id:
        sids = [session_id]
    else:
        rows = conn.execute(
            "SELECT id FROM sessions WHERE message_count > 0 ORDER BY started_at DESC LIMIT ?",
            (last,)).fetchall()
        sids = [r["id"] for r in rows]

    out = []
    for sid in sids:
        msgs = conn.execute(
            # Only count the assistant's own speech: role='tool' is tool echo output,
            # which would get mistaken for deliverable diffs/logs (hit this in practice)
            "SELECT id, session_id, role, content, timestamp FROM messages "
            "WHERE session_id=? AND active=1 AND role='assistant' AND content!='' ORDER BY id",
            (sid,)).fetchall()
        if mode == "final":
            # Look back up to FINAL_LOOKBACK messages, taking the **most recent** message
            # that contains a deliverable element (the report is often not the very last line)
            for m in reversed(msgs[-FINAL_LOOKBACK:]):
                txt = m["content"] or ""
                if not txt.strip():
                    continue
                sig = extract_signals(txt)
                if is_report_card(txt, sig, "final"):
                    out.append((dict(m), sig))
                    break
            continue
        picked = 0
        for m in msgs:
            txt = m["content"] or ""
            sig = extract_signals(txt)
            if is_report_card(txt, sig, mode):
                out.append((dict(m), sig))
                picked += 1
                if picked >= max_per_session:
                    break
    conn.close()
    return out


def run(db_path: str, session_id: str | None = None, last: int = 1,
        push: bool = False, dry_run: bool = False, mode: str = "final") -> dict:
    """Main flow: extract cards → write locally → optionally push into the Memory Palace. Returns stats."""
    idx = load_index()
    found = fetch_report_messages(db_path, session_id, last, mode)
    fresh, dup = [], 0
    for msg, sig in found:
        card = build_card(msg, sig)
        d = card_digest(card)
        if d in idx:
            dup += 1
            continue
        card["digest"] = d
        fresh.append(card)

    result = {"session_id": session_id or f"last{last}", "mode": mode, "cards_found": len(found),
              "new_cards": len(fresh), "duplicates": dup,
              "pushed": 0, "would_push": len(fresh) if push else 0,
              "dry_run": dry_run, "local_file": CARDS_FILE,
              "cards": [{"msg_id": c["msg_id"], "time": c["time"],
                         "signals": c["signals"], "paths": c["paths"][:3]} for c in fresh]}

    if dry_run:
        result["preview"] = [card_text(c)[:400] for c in fresh[:2]]
        return result

    if fresh:
        append_cards(fresh)
        if push:
            for c in fresh:
                r = push_card(c)
                ok = bool(r.get("stored") or r.get("memory_id") or r.get("id"))
                idx[c["digest"]] = {"pushed_at": datetime.now().isoformat(timespec="seconds"),
                                    "memory_id": r.get("memory_id") or r.get("id"),
                                    "ok": ok, "session_id": c["session_id"], "msg_id": c["msg_id"]}
                result["pushed"] += 1 if ok else 0
        else:
            for c in fresh:   # even local-only runs need to record the index, to avoid re-extracting
                idx[c["digest"]] = {"pushed_at": None, "session_id": c["session_id"],
                                    "msg_id": c["msg_id"], "ok": False}
        save_index(idx)
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Report-card extraction → local JSONL + Memory Palace")
    ap.add_argument("--session-id", help="a specific session")
    ap.add_argument("--last", type=int, default=1, help="the last N sessions (default 1)")
    ap.add_argument("--push", action="store_true", help="push into the Memory Palace (category=worklog)")
    ap.add_argument("--dry-run", action="store_true", help="preview only, write nothing")
    ap.add_argument("--mode", choices=["final", "scan", "all"], default="final",
                    help="final=only the final report per session (default); scan=full scan (strict); all=loosest (analysis only)")
    ap.add_argument("--list", action="store_true", help="list already-extracted cards")
    ap.add_argument("--db", default=HERMES_DB, help="path to state.db (for testing)")
    args = ap.parse_args()

    if args.list:
        idx = load_index()
        if not os.path.exists(CARDS_FILE):
            print("(no cards yet)")
            sys.exit(0)
        with open(CARDS_FILE) as f:
            for line in f:
                c = json.loads(line)
                print(f"  msg#{c['msg_id']:<7} {c['time']}  {c['session_id'][:22]}  "
                      f"signals:{','.join(c['signals'])}  pushed:{'yes' if idx.get(c.get('digest'), {}).get('pushed_at') else 'no'}")
        sys.exit(0)

    out = run(args.db, args.session_id, args.last, args.push, args.dry_run, args.mode)
    print(json.dumps(out, ensure_ascii=False, indent=2))
