#!/usr/bin/env python3
"""
Memory gateway — smart routing between production and local
Usage:
  python3 memory_gateway.py store --content "..." [--category fact] [--user default]
  python3 memory_gateway.py status
  python3 memory_gateway.py push [--batch 50]
"""
import sys
import os
import json
import argparse
import httpx
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
from local_cache import store_memory, get_stats, init_db
from sync_push import push_batch, check_gz_online

GZ_API = "http://127.0.0.1:18010"
MEMORY_ENDPOINT = f"{GZ_API}/api/v1/memories"
TIMEOUT = 10


def store_to_gz(content: str, category: str = "fact", user_id: str = "default",
                importance: float = 0.5) -> dict:
    """Try writing to production Mnemosyne"""
    payload = {
        "content": content,
        "category": category,
        "user_id": user_id,
        "importance": importance,
    }
    try:
        r = httpx.post(
            MEMORY_ENDPOINT,
            json=payload,
            timeout=TIMEOUT,
            params={"user_id": user_id}
        )
        if r.status_code == 200:
            data = r.json()
            return {"ok": True, "target": "gz", "id": data.get("id"), "content": content[:100]}
        return {"ok": False, "target": "gz", "error": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"ok": False, "target": "gz", "error": str(e)[:100]}


def smart_store(content: str, category: str = "fact", user_id: str = "default",
                importance: float = 0.5) -> dict:
    """Smart store: try production first → fall back to local SQLite on failure"""
    init_db()
    
    # Try production first
    result = store_to_gz(content, category, user_id, importance)
    if result["ok"]:
        return result
    
    # Production unavailable, store locally
    local_id = store_memory(content, category, user_id, importance)
    pending = get_stats()["pending"]
    return {
        "ok": True,
        "target": "local",
        "local_id": local_id,
        "pending_total": pending,
        "gz_error": result.get("error", "offline"),
        "content": content[:100]
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Memory gateway — dual-write to production/local")
    sub = parser.add_subparsers(dest="cmd")
    
    store_p = sub.add_parser("store", help="Store a memory")
    store_p.add_argument("--content", required=True, help="Memory content")
    store_p.add_argument("--category", default="fact")
    store_p.add_argument("--user", default="default")
    store_p.add_argument("--importance", type=float, default=0.5)
    
    sub.add_parser("status", help="View local cache status")
    
    push_p = sub.add_parser("push", help="Push the local cache to production")
    push_p.add_argument("--batch", type=int, default=50)
    
    sub.add_parser("check", help="Check production connectivity")
    
    args = parser.parse_args()
    
    if args.cmd == "store":
        result = smart_store(args.content, args.category, args.user, args.importance)
        print(json.dumps(result, ensure_ascii=False))
    
    elif args.cmd == "status":
        stats = get_stats()
        online = check_gz_online()
        print(json.dumps({**stats, "gz_online": online}, ensure_ascii=False))
    
    elif args.cmd == "push":
        result = push_batch(args.batch)
        print(json.dumps(result, ensure_ascii=False))
    
    elif args.cmd == "check":
        online = check_gz_online()
        print(json.dumps({"gz_online": online, "endpoint": GZ_API}))
    
    else:
        parser.print_help()
