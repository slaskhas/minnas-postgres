#!/usr/bin/env python3
"""
Session archiving — Hermes conversations → Mnemosyne Memory Palace
Usage:
  python3 archive_session.py                    # archive the most recent session
  python3 archive_session.py --session-id ID    # archive a specific session
  python3 archive_session.py --last N           # archive the last N sessions
  python3 archive_session.py --dry-run          # preview without pushing
  python3 archive_session.py --auto             # auto-archive the first un-archived session (for hooks/cron)
  python3 archive_session.py --list             # list recent sessions

v7.8.4 (2026-09-24) three archiving-quality fixes — triggered by: deliverable paths/hashes/measured
numbers inside the final report were not found at the semantic-search layer
  1. Tiered truncation: the final report + user messages are **kept in full**; only process
     messages get compressed.
     The old version blanket-truncated every message with `content[:2000]`, but reports are
     often >2000 chars — exactly the part that got cut was the valuable part.
  2. Evidence signature: messages carrying tool_calls get a `⟪工具: terminal×3, read_file×2⟫`
     suffix appended, so the semantic layer can see "what happened in this turn".
  3. Short sessions are no longer dropped: message_count < 5 is now stored with a `[短]` prefix
     instead (the old version just skipped it → small tasks left no trace).
"""
import collections
import re
import sqlite3
import json
import sys
import os
import argparse
import urllib.request
from datetime import datetime, timezone

HERMES_DB = os.path.expanduser("~/.hermes/state.db")
MNEMOSYNE_API = "http://127.0.0.1:18010/api/v1/sessions/archive"
TRACKING_FILE = os.path.expanduser("~/.hermes/archived_sessions.json")

# ---- v7.8.4 tiered-truncation budget ----
LIMIT_FINAL_REPORT = 12000   # last AI message of the session (final report) — kept in full
LIMIT_USER = 8000            # user messages — kept in full (cap only guards against extreme pastes)
LIMIT_PROCESS = 1200         # process messages (tool round-trips/intermediate analysis) — compressed
MAX_TOTAL = 120000           # total budget per session (the old version measured 125k chars archived
                             # for one session in production with no issues, so this isn't set lower)
                             # once over budget, only "process messages" yield space; protected
                             # content (report/user messages) is never trimmed
TOOL_SIG_MAX = 5             # max number of distinct tools listed in the evidence signature
SHORT_SESSION = 5            # below this count it's a "short session": still stored, but tagged [短]

# Project keywords (loaded from a config file; returns empty if absent)
KEYWORDS_FILE = os.path.join(os.path.dirname(__file__), "project_keywords.json")
try:
    with open(KEYWORDS_FILE) as f:
        PROJECT_KEYWORDS = json.load(f)
    # Drop comment keys
    PROJECT_KEYWORDS = {k: v for k, v in PROJECT_KEYWORDS.items() if not k.startswith("_")}
except (FileNotFoundError, json.JSONDecodeError):
    PROJECT_KEYWORDS = {}


def _compose_title(title, proj: str | None, is_short: bool) -> str:
    """Compose [project] / [短] into a **single** prefix tag, idempotently.

    The old approach of prepending twice produced `[短] [relife] title` —— nested
    brackets that would keep stacking on repeated calls.
    """
    title = str(title or "")
    m = re.match(r"^\[([^\]]*)\]\s*(.*)$", title)
    tag, rest = (m.group(1), m.group(2)) if m else ("", title)
    parts = [p.strip() for p in tag.split("·") if p.strip()]
    if is_short and "短" not in parts:
        parts.insert(0, "短")
    if proj and proj not in parts:
        parts.append(proj)
    return "[{}] {}".format("·".join(parts), rest) if parts else rest


def _detect_project(text: str) -> str:
    """Detect which project the text belongs to (requires >=2 keyword hits, to avoid single-word false matches)"""
    t = text.lower()
    best, best_score = None, 0
    for proj, words in PROJECT_KEYWORDS.items():
        s = sum(1 for w in words if w.lower() in t)
        if s > best_score:
            best, best_score = proj, s
    return best if best_score >= 2 else None


# ---- v7.8.4: evidence signature ----

def _tool_names(tool_calls) -> list:
    """Extract the list of tool names from tool_calls (OpenAI-shaped), tolerating any malformed input."""
    if not tool_calls:
        return []
    try:
        items = json.loads(tool_calls)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(items, list):
        items = [items]
    names = []
    for it in items:
        if not isinstance(it, dict):
            continue
        nm = None
        fn = it.get("function")
        if isinstance(fn, dict):
            nm = fn.get("name")
        if not nm:
            nm = it.get("name")
        if isinstance(nm, str) and nm.strip():
            names.append(nm.strip())
    return names


def tool_signature(tool_calls) -> str:
    """Produces `⟪工具: terminal×3, read_file×2⟫` — empty string if there are no tools."""
    names = _tool_names(tool_calls)
    if not names:
        return ""
    counts = collections.Counter(names)
    top = ", ".join(f"{n}×{c}" if c > 1 else n for n, c in counts.most_common(TOOL_SIG_MAX))
    extra = f", …共{len(counts)}种" if len(counts) > TOOL_SIG_MAX else ""
    return f" ⟪工具: {top}{extra}⟫"


def _last_report_id(messages) -> int | None:
    """The session's last AI message with body text = the final report (the one kept in full)."""
    for msg in reversed(messages):
        if msg["role"] != "user" and (msg["content"] or "").strip():
            return msg["id"]
    return None


def format_session(messages) -> tuple:
    """Assemble the session body with tiered truncation. Returns (content, stats).

    Budget rule: the final report (last AI message) and user messages are never trimmed;
    once the total length exceeds MAX_TOTAL, compression yields space starting from the
    "earliest process message".
    """
    final_id = _last_report_id(messages)
    stats = {"messages": 0, "final_report_chars": 0, "truncated_process": 0, "budget_trimmed": 0}

    entries = []  # (label, content, sig, protected)
    for msg in messages:
        role_label = "用户" if msg["role"] == "user" else "AI"
        content = (msg["content"] or "").strip()
        sig = tool_signature(msg["tool_calls"] if "tool_calls" in msg.keys() else None)
        if not content and not sig:
            continue
        is_final = (msg["id"] == final_id)
        protected = is_final or msg["role"] == "user"
        limit = LIMIT_FINAL_REPORT if is_final else (LIMIT_USER if msg["role"] == "user" else LIMIT_PROCESS)
        if len(content) > limit:
            dropped = len(content) - limit
            content = content[:limit] + f"...(截断 {dropped} 字)"
            if not protected:
                stats["truncated_process"] += 1
        if is_final:
            stats["final_report_chars"] = len(content)
        entries.append([role_label, content, sig, protected])

    total = sum(len(f"{e[0]}: {e[1]}{e[2]}") + 2 for e in entries)
    trimmed = set()
    while total > MAX_TOTAL:
        cand = [i for i, e in enumerate(entries) if not e[3] and len(e[1]) > 220]
        if not cand:
            break                        # only "protected" content remains → allow going over budget (report takes priority)
        i = cand[0]                      # the earliest process message yields first
        before = len(entries[i][1])
        new_len = max(200, before // 2)
        entries[i][1] = entries[i][1][:new_len] + "...(预算压缩)"
        total -= before - len(entries[i][1])   # net reduction (including the suffix; the old version miscounted the suffix → looped more than needed)
        trimmed.add(i)
    stats["budget_trimmed"] = len(trimmed)     # semantics: number of "messages" that yielded space

    lines = [f"{e[0]}: {e[1]}{e[2]}" for e in entries]
    stats["messages"] = len(lines)
    return "\n\n".join(lines), stats


def get_session(db_path: str, session_id: str = None) -> dict:
    """Fetch a session from the Hermes DB (v7.8.4: also fetches tool_calls; short sessions are no longer excluded)"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    if session_id:
        session = conn.execute(
            "SELECT * FROM sessions WHERE id=? ORDER BY started_at DESC LIMIT 1",
            (session_id,)
        ).fetchone()
    else:
        session = conn.execute(
            "SELECT * FROM sessions WHERE message_count > 3 ORDER BY started_at DESC LIMIT 1"
        ).fetchone()

    if not session:
        conn.close()
        return None

    try:
        messages = conn.execute(
            "SELECT id, role, content, tool_calls FROM messages WHERE session_id=? AND active=1 "
            "ORDER BY id",
            (session["id"],)
        ).fetchall()
    except sqlite3.OperationalError:
        # Older DB has no tool_calls column → fall back to fetching body text only
        messages = conn.execute(
            "SELECT id, role, content, '' AS tool_calls FROM messages "
            "WHERE session_id=? AND active=1 ORDER BY id",
            (session["id"],)
        ).fetchall()

    conn.close()

    content, stats = format_session(messages)

    # v7.8.4: wires up the project prefix (verified in the deployed copy on 2026-09-04) + short-session tag, composed into a single label
    proj = _detect_project(content)
    title = _compose_title(session["title"], proj,
                           session["message_count"] < SHORT_SESSION)

    return {
        "session_id": session["id"],
        "title": title,
        "content": content,
        "message_count": session["message_count"],
        "started_at": session["started_at"],
        "stats": stats,
    }


def archive_to_mnemosyne(session: dict, dry_run: bool = False) -> dict:
    """Push a session to the Memory Palace (title already includes the [project]/[短] prefix)"""
    payload = json.dumps({
        "user_id": "default",
        "session_id": session["session_id"],
        "title": session["title"],
        "content": session["content"],
    }).encode()

    if dry_run:
        return {
            "dry_run": True,
            "would_send": len(session["content"]),
            "title": session["title"],
            "stats": session.get("stats"),
            "preview": session["content"][:200],
        }

    try:
        req = urllib.request.Request(
            MNEMOSYNE_API,
            data=payload,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return {"archived": False, "error": str(e)}


def list_sessions(db_path: str, limit: int = 10) -> list:
    """List recent sessions"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, title, message_count, started_at FROM sessions "
        "WHERE message_count > 0 ORDER BY started_at DESC LIMIT ?",
        (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def load_archived() -> set:
    """Load the set of already-archived session IDs"""
    try:
        with open(TRACKING_FILE) as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_archived(archived: set):
    """Save archived session IDs"""
    with open(TRACKING_FILE, 'w') as f:
        json.dump(list(archived), f)


def auto_mode(include_short: bool = True):
    """Auto mode: archive the first un-archived, completed session

    v7.8.4: short sessions are no longer skipped — they're now stored with a [短] prefix
    (pass include_short=False to restore the old behavior).
    """
    archived = load_archived()
    sessions = list_sessions(HERMES_DB, limit=20)

    for s in sessions:
        sid = s["id"]
        if sid in archived:
            continue
        if not include_short and s["message_count"] < SHORT_SESSION:
            continue

        session = get_session(HERMES_DB, sid)
        if not session:
            continue

        result = archive_to_mnemosyne(session)
        if result.get("archived"):
            archived.add(sid)
            save_archived(archived)
            print(json.dumps({"auto_archived": True, "session_id": sid[:20],
                            "memory_id": result.get("memory_id"), "title": s["title"],
                            "messages": s["message_count"],
                            "stats": session.get("stats")}, ensure_ascii=False))
            return result

        # Even a duplicate counts as archived
        if result.get("reason") == "duplicate":
            archived.add(sid)
            save_archived(archived)

    return {"auto_archived": False, "reason": "nothing_new"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hermes session archiving → Mnemosyne")
    parser.add_argument("--session-id", help="archive a specific session")
    parser.add_argument("--last", type=int, default=1, help="archive the last N sessions")
    parser.add_argument("--dry-run", action="store_true", help="preview")
    parser.add_argument("--list", action="store_true", help="list archivable sessions")
    parser.add_argument("--auto", action="store_true", help="auto-archive the first un-archived session (for cron/hooks)")
    parser.add_argument("--skip-short", action="store_true",
                        help="v7.8.4 compatibility switch: restore the old behavior (skip sessions with <5 messages)")
    args = parser.parse_args()

    if args.auto:
        result = auto_mode(include_short=not args.skip_short)
        sys.exit(0 if result.get("auto_archived") else 0)

    if args.list:
        sessions = list_sessions(HERMES_DB)
        for s in sessions:
            print(f"  {s['id'][:12]}...  [{s['message_count']} msgs] {s['title'] or '(untitled)'}  {str(s['started_at'])[:19]}")
        sys.exit(0)

    for i in range(args.last):
        sid = args.session_id if args.session_id else None
        session = get_session(HERMES_DB, sid)

        if not session:
            print("No sessions found.")
            sys.exit(1)

        result = archive_to_mnemosyne(session, args.dry_run)
        print(json.dumps(result, ensure_ascii=False, indent=2))

        if args.session_id:
            break  # a specific session ID only runs once
