"""
Mnemosyne v5.0 — Hermes MemoryProvider SDK
Whitepaper §6.2 workflow-level deep mapping + §6.4 official native SDK

Zero-intrusion replacement for Hermes' native memory base class
"""
import json
import urllib.request
from typing import Dict, List, Optional


class MnemosyneHermesMemory:
    """
    Hermes native SDK — 3-line integration
    
    Usage:
        from mnemosyne_hermes_sdk import MnemosyneHermesMemory
        memory = MnemosyneHermesMemory(endpoint="http://127.0.0.1:18010")
        memory.add("User prefers Python 3.10", memory_type="preference")
    """
    
    def __init__(self, endpoint: str = "http://127.0.0.1:18010", 
                 api_key: str = "", 
                 user_id: str = "default",
                 agent_id: str = "hermes-main"):
        self.endpoint = endpoint.rstrip("/")
        self.user_id = user_id
        self.agent_id = agent_id
        self.session_id = None
        self.decision_level = "L0"  # L0/L1/L2
        
        self._opener = urllib.request.build_opener()
        if api_key:
            self._opener.addheaders = [("Authorization", f"Bearer {api_key}")]
    
    def _post(self, path: str, data: dict) -> dict:
        url = f"{self.endpoint}{path}"
        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode(),
            headers={
                "Content-Type": "application/json",
                "X-Agent-Id": self.agent_id,
            },
            method="POST"
        )
        with self._opener.open(req, timeout=30) as resp:
            return json.loads(resp.read())
    
    def _get(self, path: str) -> dict:
        url = f"{self.endpoint}{path}"
        req = urllib.request.Request(url, method="GET")
        with self._opener.open(req, timeout=10) as resp:
            return json.loads(resp.read())
    
    # ── Session management ──
    def on_session_start(self, session_id: str):
        """Session start"""
        self.session_id = session_id
    
    # ── Memory operations ──
    def add(self, content: str, memory_type: str = "general", 
            category: str = "knowledge", tags: List[str] = None) -> dict:
        """Add a memory → research hall (v6.0: category uses a controlled vocabulary, auto-normalized server-side)"""
        return self._post("/api/v1/halls/archive", {
            "content": content,
            "memory_type": memory_type,
            "category": category,
            "tags": tags or [],
            "session_id": self.session_id,
            "tenant_id": self.user_id,
        })
    
    def get_relevant(self, query: str, top_k: int = 3) -> List[dict]:
        """Retrieve relevant memories"""
        r = self._post("/api/v1/memories/search", {
            "query": query,
            "top_k": top_k,
            "user_id": self.user_id,
        })
        data = r.get("data", r)  # tolerate both wrapped and unwrapped responses
        return data.get("memories", [])
    
    def search_by_hall(self, hall: str, limit: int = 10) -> List[dict]:
        """Query by hall"""
        r = self._get(f"/api/v1/halls/{hall}?tenant_id={self.user_id}&limit={limit}")
        data = r.get("data", r)
        return data.get("memories", [])
    
    # ── Tool calls ──
    def archive_tool_call(self, tool_name: str, params: dict,
                          result: str, success: bool,
                          error_type: str = None, duration_ms: int = None) -> dict:
        """Archive a tool call result"""
        return self._post("/api/v1/tools/archive", {
            "tool_name": tool_name,
            "params": params,
            "result": str(result),
            "success": success,
            "error_type": error_type,
            "session_id": self.session_id,
            "duration_ms": duration_ms,
            "tenant_id": self.user_id,
        })
    
    # ── Project management ──
    def start_project(self, project_name: str, description: str = "") -> dict:
        """Create a project"""
        return self._post("/api/v1/projects/create", {
            "name": project_name,
            "description": description,
            "tenant_id": self.user_id,
        })
    
    def archive_project(self, project_id: int) -> dict:
        """Archive a project"""
        return self._post(f"/api/v1/projects/{project_id}/archive", {})
    
    # ── Security operations ──
    def purify_memory(self, memory_id: int, reason: str = "user_request") -> dict:
        """Hash-purify a memory"""
        return self._post("/api/v1/security/purify", {
            "memory_id": memory_id,
            "reason": reason,
        })
    
    def run_audit(self, limit: int = 5) -> dict:
        """Run a heterogeneous audit"""
        return self._post(f"/api/v1/security/audit/run?limit={limit}", {})
    
    def get_costs(self) -> dict:
        """Query cost"""
        return self._get("/api/v1/security/costs")
    
    # ── Configuration ──
    def set_decision_level(self, level: str):
        """Set decision level L0/L1/L2"""
        self.decision_level = level
    
    def enable_anonymous_feedback(self, enabled: bool = True):
        """Enable anonymous feedback reporting"""
        self._feedback_enabled = enabled
    
    # ── Stats ──
    def stats(self) -> dict:
        """Memory store stats"""
        return self._get(f"/api/v1/memories/stats?user_id={self.user_id}")
    
    # ── Shortcut methods ──
    def remember(self, content: str, category: str = "note") -> dict:
        """Quick memory (archives to the archive hall)"""
        return self.add(content, memory_type="fact", category=category)
    
    def pitfall(self, tool: str, error: str, fix: str = "") -> dict:
        """Record a pitfall"""
        return self.archive_tool_call(
            tool_name=tool,
            params={},
            result=f"{error} || FIX: {fix}" if fix else error,
            success=False,
            error_type="user_reported"
        )
