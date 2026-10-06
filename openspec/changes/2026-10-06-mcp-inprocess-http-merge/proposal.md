---
提案号: P-20261006-03
提案者: CLAUDE CODE
时间: 2026-10-06T00:00+00:00
目标: integrations/hermes-mcp + main.py
动作: ADDED (/mcp streamable-HTTP) ; MODIFIED (stdio 桥保留不动)
依据: 用户指令 "do alternative A, implement"；tests/test_mcp_bridge_contract.py 锁定出站形；AGENTS.md 集成契约红线
状态: pending
冲突: []
旧版: -
---

# 变更提案：MCP 桥内进程化 —— stdio 桥合并进 core 进程，15 个工具经 streamable HTTP 挂在 /mcp

- **日期**：2026-10-06
- **档位**：M（跨文件；新增对外端点 `/mcp`；stdio 不变）
- **关联**：`openspec/specs/mcp-transport.md` · ADR-0003 · v7.8.2 契约教训（422 红线）

## 为什么改（Why）

现状：15 个 Mnemosyne MCP 工具由独立 stdio 子进程（`integrations/hermes-mcp/mnemosyne_mcp.py`）承载，
经 Hermes / slask 客户端走 SSH 隧道（18010 → core 8010）启动。带来三类成本：

1. **多进程 + 多隧道**：Hermes 端要起子进程，且需一条 SSH 隧道到 core；bridge 与 core 是两个进程、两个 REST 客户端。
2. **版本漂移**：bridge 独立演进时，`/api/v1` 契约可能与 core 脱节（v7.8.2 的 422 即此类）。
3. slask TS 客户端是 **streamable-HTTP 优先** —— 原生支持；stdio 反而是次选。

**Alternative A（本提案）**：把 bridge 的**同一批** contract-tested 处理器
（`_dispatch`/`_call`/`list_tools`/`call_tool`）挂进 core 的 uvicorn 进程，经 **streamable HTTP** 暴露在 `/mcp`。
stdio 保留不动（Hermes 存量 / `mcp_adapt_test.py` 继续可用）；两个 transport 共享一个 low-level `Server`。
处理器仍走 **loopback REST** 调用 core（`set_base_url()`），**不猜字段名/参数位** —— 完全复用已验证的 contract 路径。

## 改什么（What）

- `integrations/hermes-mcp/mnemosyne_mcp.py`
  - 新增 `from __future__ import annotations`（注解延迟求值，`types.*` 仅在 mcp 在场时被求值）。
  - 新增 guarded mcp import → `_MCP_AVAILABLE`；mcp 缺失时 `main.py` 可无它导入。
  - 新增 `set_base_url(url)`：重算 `MNEMOSYNE_URL`/`API_BASE`，重建 `httpx` 客户端（`_call` 从 `API_BASE` 解析绝对 URL）。
  - 新增 `build_server() -> Server`（复用全局 low-level `Server`）与 `build_http_app() -> Starlette app`
    （`server.streamable_http_app(streamable_http_path="/mcp", stateless_http=True)`）。
  - 新增 `__main__` guard（stdio 入口不变）；mcp 缺失时给 stub，调用抛 `RuntimeError`。
  - **`_dispatch`/`_call`/`list_tools`/`call_tool` 逐字未动**（红线：契约代码不动）。
- `main.py`
  - 在 `injection_router` 后按**文件路径**（父目录 `hermes-mcp` 含连字符 → 非合法 import 名）加载 bridge，
    检查 `_MCP_AVAILABLE` + `set_base_url`/`build_http_app` 可调用，则
    `set_base_url(http://<HOST>:<PORT>)`（`0.0.0.0`/空 → `127.0.0.1`）并 `app.mount("/mcp", build_http_app())`。
  - `try/except` 包裹：mcp 缺失或 bridge 变更 → 仅 DEBUG 日志，REST API 不受影响。
  - `stateless_http=True`：请求无状态 → `uvicorn --workers N` 无跨 worker 会话漂移。
- `tests/test_mcp_http_mount.py`（新增）
  - `test_set_base_url_repoints_calling_layer`：`set_base_url` 重算 `API_BASE`/`MNEMOSYNE_URL`、重建 client。
  - `test_build_server_lists_all_tools`：真实 in-process `mcp.Client(server)` 驱动，列恰好 15 个工具名。
  - `test_build_http_app_is_starlette_with_mcp_route`：`build_http_app()` 返回带 `/mcp` 路由的 Starlette app。
  - `pytest.importorskip("mcp")`：mcp 缺失（minimal install）则跳过。
- `requirements.txt`
  - 新增 `mcp>=2.0`（注释注明"REST 核心无它也跑，mount 自动跳过"）。

## 不改什么（Out of Scope）

- ❌ 不动 `tests/test_mcp_bridge_contract.py`（按文件路径加载 bridge；`_dispatch`/`_call` 未动 → 仍绿）。
- ❌ 不动 stdio 入口（Hermes 现有配置 / `scripts/mcp_adapt_test.py` 继续工作）。
- ❌ 不动 `/api/v1` 任何端点、字段名、参数位。
- ❌ 不动 Nginx 鉴权层（`/mcp` 沿用 loopback 直连，无鉴权，与 REST 直连一致）。

## 验收标准

- [x] 三处语法绿（`py_compile` bridge / test / main）。
- [x] 无 mcp 的 env：bridge 可导入、stub 抛 `RuntimeError`；`main.py` 的 mount 优雅跳过（REST 不受影响）。
- [ ] 带 mcp 的 env：`build_http_app()` 返回含 `/mcp` 的 Starlette app；in-process `Client(build_server())` 列 15 工具。
- [ ] 带 DB 的 env：`POST /mcp`（`initialize`，`Content-Type: application/json` + 合法 Host + SSE `Accept`）→ 200，SSE 含 `"mnemosyne"`；一个工具 round-trip。
- [x] 既有 contract 测试（`test_mcp_bridge_contract.py`）不受影响。
- [ ] 隐私扫描（push 后）零输出；VERSION / README badge（EN+CN）/ CHANGELOG 三处版本号一致。

## 风险与回滚

- 风险面：`set_base_url` 使 handler 指向 loopback；若 core 直连层将来加鉴权（当前 Nginx-only，直连无鉴权）会失败。
- 多 worker：`stateless_http=True` 保证无跨 worker 状态漂移。
- 回滚：`git revert` 本变更；stdio 路径独立存在，不受影响，Hermes 仍可走 tunnel。
