# MCP Transport（能力真相 · 可执行规格）

> 权威实现：`integrations/hermes-mcp/mnemosyne_mcp.py` + `main.py`（`/mcp` mount）
> 对外入口：`GET /mcp`（streamable HTTP / SSE）· `GET /api/v1/capabilities`（唯一事实源）
> 状态：**生效中**（v8.1.0）｜ 来源：用户指令 "do alternative A, implement" → 归一化 → 可执行化

## 1. 两种 transport，一个 Server

**一个 low-level `mcp.server.lowlevel.Server(name="mnemosyne")`** 同时服务两个 transport：

| transport | 入口 | 谁启动 |
|---|---|---|
| stdio | `python3 integrations/hermes-mcp/mnemosyne_mcp.py`（`__main__`） | Hermes 子进程 / `scripts/mcp_adapt_test.py`；经 SSH 隧道 18010→8010 |
| streamable HTTP | `GET /mcp`（同进程 mount，`main.py`） | core 的 uvicorn 进程自身；`stateless_http=True` |

两者共享**同一批** contract-tested 处理器：`list_tools`（15 工具）、`_dispatch`、`_call`。

## 2. 15 个工具

| 族 | 工具 |
|---|---|
| 记忆核心 | `store_memory` · `search_memories` · `dialectic_search` · `get_hot_memories` |
| 记忆管理 | `get_memory_stats` · `feedback_memory` · `get_memory_traces` · `delete_memory` · `restore_memory` |
| 知识图谱 | `search_graph` · `extract_entities` |
| Wiki | `create_wiki_page` · `search_wiki` |
| 信念 | `store_belief` · `search_beliefs` |

## 3. 参数位与字段名（红线）

- **参数位照 server**：`feedback_memory`/`delete_memory`/`restore_memory` 的 `user_id`（及 `feedback_memory` 的 `feedback`）是 **query** 参数 —— 放 JSON body 或省略 → 422。
- **字段名照 capabilities/schema**：如 `heat-top` 返回 `heat_score`（非 `heat`）；**读错字段不报错、静默回退默认**，比报错更危险。
- 唯一事实源：`GET /api/v1/capabilities`。任何 handler / integration 变更必须跑 `tests/test_mcp_bridge_contract.py`。

## 4. in-process 挂载（v8.1 新增）

`main.py` 启动时按**文件路径**加载 bridge（父目录 `hermes-mcp` 含连字符 → 非合法 import 名）。
若 `_MCP_AVAILABLE` 且 `set_base_url`/`build_mcp_mount` 可调用，在 app 的 **lifespan**（`asynccontextmanager`；`app = FastAPI(..., lifespan=_mcp_lifespan)`）内：

1. `set_base_url("http://<HOST>:<PORT>")`（`0.0.0.0`/空 → `127.0.0.1`）—— 让 handler 经 **loopback REST** 调 core（直连 uvicorn，无 Nginx 鉴权）。
2. `asgi_endpoint, run_cm = build_mcp_mount()` → `_app.router.add_route("/mcp", asgi_endpoint, methods=None, include_in_schema=False)`，并在 lifespan 中 `async with run_cm():`（`run_cm = session_manager.run()`，须进入以初始化 task group，否则端点 500）。
   - **不用** `app.mount("/mcp", build_http_app())`：Starlette `Mount` **从不运行子应用 lifespan**（→ "Task group is not initialized" 500），且子应用自带 `/mcp` 路由，套在 `/mcp` 前缀下会嵌套成 `/mcp/mcp`。原始 per-endpoint ASGI app（方法在 body、与 URL 路径无关）用 `methods=None` 精确挂到 `/mcp`（全方法、无尾斜杠 307），与 SDK 自身 standalone app（`Route(path, sm.asgi_app, methods=None)` + `lifespan=lambda app: sm.run()`）同构。

**graceful degradation**：`mcp` 缺失（minimal install）或 bridge 变更 → 仅 DEBUG 日志，REST API 不受影响。
**`stateless_http=True`**（`build_mcp_mount` 内部 `streamable_http_path="/mcp", stateless_http=True`）：每请求独立 → `uvicorn --workers N` 无跨 worker 会话漂移。

## 5. 验收锚点（可证伪）

1. `build_http_app()` 返回 Starlette app 且路由含 `/mcp`
   （`tests/test_mcp_http_mount.py::test_build_http_app_is_starlette_with_mcp_route`）。
2. in-process `mcp.Client(build_server())` 列出**恰好** 15 个工具
   （`...::test_build_server_lists_all_tools`）。
3. `set_base_url("http://127.0.0.1:8010")` 后 `API_BASE == "http://127.0.0.1:8010/api/v1"`
   （`...::test_set_base_url_repoints_calling_layer`）。
4. 真实 HTTP（带 DB）：`POST /mcp` `initialize`（`Content-Type: application/json` + `Accept` 含 `application/json, text/event-stream` + 合法 Host）→ 200，SSE 含 `"mnemosyne"`。
5. `tests/test_mcp_bridge_contract.py` 不受本变更影响（`_dispatch`/`_call` 逐字未动）。

## 6. 不做什么

- 不动 stdio 入口（向后兼容 Hermes 存量 / `mcp_adapt_test.py`）。
- 不动 `/api/v1` 端点 / 字段名 / 参数位。
- 不跨 worker 维护 MCP 会话状态（stateless 优先）；出现状态需求 → 触发 ADR-0003 T-1。
