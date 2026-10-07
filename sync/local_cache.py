"""
Edge-to-cloud incremental sync — local SQLite cache on WSL
Production online → write directly to production
Production offline → write to local SQLite, push once it recovers
"""
import sqlite3
import json
import os
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(__file__), "local_cache.db")


def init_db():
    """Initialize the local cache tables"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pending_memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            category TEXT DEFAULT 'fact',
            user_id TEXT DEFAULT 'default',
            importance REAL DEFAULT 0.5,
            embedding_json TEXT,
            created_at TEXT NOT NULL,
            synced INTEGER DEFAULT 0,
            sync_at TEXT,
            error_msg TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sync_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL,
            count INTEGER DEFAULT 0,
            detail TEXT,
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def store_memory(content: str, category: str = "fact", user_id: str = "default",
                 importance: float = 0.5, embedding: list = None) -> int:
    """Store in the local cache, returns the local ID"""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    now = datetime.now(timezone.utc).isoformat()
    emb_str = json.dumps(embedding) if embedding else None
    cursor = conn.execute(
        "INSERT INTO pending_memories (content, category, user_id, importance, embedding_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (content, category, user_id, importance, emb_str, now)
    )
    local_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return local_id


def get_pending_count() -> int:
    """Count of items pending push"""
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT count(*) FROM pending_memories WHERE synced=0").fetchone()
    conn.close()
    return row[0] if row else 0


def get_pending(batch_size: int = 50) -> list:
    """Fetch memories pending push"""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT id, content, category, user_id, importance, embedding_json, created_at "
        "FROM pending_memories WHERE synced=0 ORDER BY id LIMIT ?",
        (batch_size,)
    ).fetchall()
    conn.close()
    return [{
        "local_id": r[0], "content": r[1], "category": r[2],
        "user_id": r[3], "importance": r[4], "embedding": json.loads(r[5]) if r[5] else None,
        "created_at": r[6]
    } for r in rows]


def mark_synced(local_id: int):
    """Mark as pushed"""
    conn = sqlite3.connect(DB_PATH)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE pending_memories SET synced=1, sync_at=? WHERE id=?",
        (now, local_id)
    )
    conn.commit()
    conn.close()


def mark_failed(local_id: int, error: str):
    """Mark push as failed"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "UPDATE pending_memories SET error_msg=? WHERE id=?",
        (error, local_id)
    )
    conn.commit()
    conn.close()


def log_sync(action: str, count: int = 0, detail: str = ""):
    """Log a sync entry"""
    conn = sqlite3.connect(DB_PATH)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO sync_log (action, count, detail, created_at) VALUES (?,?,?,?)",
        (action, count, detail, now)
    )
    conn.commit()
    conn.close()


def get_stats() -> dict:
    """Fetch local cache stats"""
    conn = sqlite3.connect(DB_PATH)
    total = conn.execute("SELECT count(*) FROM pending_memories").fetchone()[0]
    pending = conn.execute("SELECT count(*) FROM pending_memories WHERE synced=0").fetchone()[0]
    synced = conn.execute("SELECT count(*) FROM pending_memories WHERE synced=1").fetchone()[0]
    failed = conn.execute("SELECT count(*) FROM pending_memories WHERE error_msg IS NOT NULL").fetchone()[0]
    conn.close()
    return {"total": total, "pending": pending, "synced": synced, "failed": failed}
