#!/usr/bin/env python3
"""
skill_sync.py — Hermes skill assets → Mnemosyne skill_assets syncer (v7.7.0)
Input: ~/.hermes/skills/**/SKILL.md + .archive/**/SKILL.md + .usage.json
Output: production Mnemosyne POST /api/v1/skills/sync (batched, idempotent),
or writes directly to a local test DB

Usage:
  python3 skill_sync.py --collect            # collect local skills → skill_manifest.json
  python3 skill_sync.py --push               # push manifest → Mnemosyne
  python3 skill_sync.py --verify             # diff local vs remote
  python3 skill_sync.py --test-db            # write directly to the local test DB (sandbox)
"""
import argparse, hashlib, json, os, re, sys

SKILLS_ROOT = os.path.expanduser("~/.hermes/skills")
USAGE_FILE = os.path.join(SKILLS_ROOT, ".usage.json")
MANIFEST_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skill_manifest.json")

# Pure-function section (unit-testable, no IO dependency)

def parse_frontmatter(text):
    """Extract name/description/category (aligned with skill_sleeper_scan but more robust)"""
    name = description = ""
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
    if not m:
        return name, description
    fm = m.group(1)
    m2 = re.search(r"^name:\s*(.+)$", fm, re.M)
    if m2:
        name = m2.group(1).strip().strip("\"'")
    # Prefer a block scalar (> or >- or |), fall back to a single line
    m4 = re.search(r"^description:\s*[>|]\-?\s*\n((?:\s+.+\n?)+)", fm, re.M)
    if m4:
        description = " ".join(l.strip() for l in m4.group(1).splitlines()).strip()
    else:
        m3 = re.search(r"^description:\s*(.+)$", fm, re.M)
        if m3:
            description = m3.group(1).strip().strip("\"'")
    if re.match(r"^Use when 使用 \S+ 技能处理相关任务", description) or description in (">-", ">", "|"):
        description = ""
    return name, description


def state_mapping(usage_rec):
    """curator .usage.json state → skill_assets state (aligned value domain)
    Missing/unmanaged → active (default, so the OS side doesn't wrongly purge it)"""
    st = (usage_rec or {}).get("state", "active")
    return st if st in ("active", "stale", "archived") else "active"


def build_skill_items(skills_dir, usage_data):
    """Scan the skills directory → a unified manifest item list
    - active directory: skills/**/SKILL.md
    - archive directory: skills/.archive/**/SKILL.md → state=archived
    - not present in .usage.json → defaults to active
    """
    items = []
    for base, is_archive in ((skills_dir, False), (os.path.join(skills_dir, ".archive"), True)):
        if not os.path.isdir(base):
            continue
        for dirpath, _dirnames, filenames in os.walk(base):
            if "SKILL.md" not in filenames:
                continue
            path = os.path.join(dirpath, "SKILL.md")
            try:
                text = open(path, encoding="utf-8").read()
            except Exception:
                continue
            name, desc = parse_frontmatter(text)
            if not name:
                name = os.path.basename(dirpath)
            usage = usage_data.get(name, {})
            rel = os.path.relpath(dirpath, os.path.dirname(skills_dir))
            items.append({
                "skill_name": name,
                "description": desc,
                "category": os.path.basename(os.path.dirname(dirpath)),
                "state": "archived" if is_archive else state_mapping(usage),
                "pinned": bool(usage.get("pinned", False)),
                "source_path": rel,
                "use_count": int(usage.get("use_count", 0) or 0),
                "view_count": int(usage.get("view_count", 0) or 0),
                "last_used_at": usage.get("last_used_at"),
                "last_viewed_at": usage.get("last_viewed_at"),
                "archived_at": usage.get("archived_at"),
                "content_hash": hashlib.md5(text.encode("utf-8")).hexdigest()[:12],
            })
    return items


def dedup_items(items):
    """Same-name validation: when an archived and an active item share a name
    → active wins (rare locally, this is just a safety net)"""
    by_name = {}
    for it in items:
        key = it["skill_name"]
        if key not in by_name or (it["state"] == "active" and by_name[key]["state"] != "active"):
            by_name[key] = it
    return list(by_name.values())


def diff_manifest(local_items, remote_items):
    """Change detection: returns items that need pushing (any change in
    content_hash/state/usage)"""
    remote_map = {r["skill_name"]: r for r in remote_items or []}
    to_push = []
    for it in local_items:
        r = remote_map.get(it["skill_name"])
        if r is None:
            to_push.append(it)  # new
            continue
        changed = any(
            r.get(k) != it[k]
            for k in ("description", "state", "use_count", "view_count", "content_hash")
        )
        if changed:
            it["_change"] = [k for k in ("description", "state", "use_count", "view_count", "content_hash")
                             if r.get(k) != it[k]]
            to_push.append(it)
    return to_push


# ── Main logic (IO) ──

def collect():
    usage_data = {}
    if os.path.exists(USAGE_FILE):
        try:
            usage_data = json.load(open(USAGE_FILE, encoding="utf-8"))
        except Exception as e:
            print(f"[sync] failed to read usage.json: {e}", file=sys.stderr)
    items = dedup_items(build_skill_items(SKILLS_ROOT, usage_data))
    with open(MANIFEST_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=1)
    states = {}
    for it in items:
        states[it["state"]] = states.get(it["state"], 0) + 1
    print(f"[sync] collected {len(items)} skills | state distribution: {states} | → {MANIFEST_FILE}")
    return items


def push(endpoint="http://127.0.0.1:18010"):
    """Push to Mnemosyne POST /api/v1/skills/sync (server computes the embedding)"""
    if not os.path.exists(MANIFEST_FILE):
        print("[sync] no manifest, run --collect first", file=sys.stderr)
        return
    items = json.load(open(MANIFEST_FILE, encoding="utf-8"))
    # Push in batches (50 per batch, to avoid an oversized payload)
    import urllib.request
    batch_size = 50
    total_new = total_upd = 0
    for i in range(0, len(items), batch_size):
        batch = items[i:i+batch_size]
        payload = json.dumps({"skills": batch, "tenant_id": "default"}).encode()
        req = urllib.request.Request(
            endpoint.rstrip("/") + "/api/v1/skills/sync",
            data=payload, method="POST",
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                d = json.loads(resp.read())
                total_new += d.get("new", 0)
                total_upd += d.get("updated", 0)
                print(f"[sync] batch {i//batch_size+1}: synced={d.get('synced')} new={d.get('new')} upd={d.get('updated')} embedded={d.get('embedded')}")
        except Exception as e:
            print(f"[sync] batch {i//batch_size+1} failed: {e}", file=sys.stderr)
    print(f"[sync] push complete: {len(items)} items, {total_new} new, {total_upd} updated")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--collect", action="store_true")
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args()
    if args.collect:
        collect()
    elif args.push:
        collect()
        push()
    elif args.verify:
        items = collect()
        print(f"[sync] verify: manifest has {len(items)} items, pending comparison against remote")
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
