# 任务清单 · P-20261006-03（MCP 桥内进程化）

- [x] **bridge 改写**（`integrations/hermes-mcp/mnemosyne_mcp.py`）
  - [x] 顶部加 `from __future__ import annotations`
  - [x] guarded mcp import → `_MCP_AVAILABLE`；缺失时 stub（`build_server`/`build_http_app`/`main` 抛 `RuntimeError`）
  - [x] 新增 `set_base_url(url)`（重算 `MNEMOSYNE_URL`/`API_BASE`、重建 `httpx` client）
  - [x] 新增 `build_server()` / `build_http_app(streamable_http_path="/mcp", stateless_http=True)`
  - [x] `__main__` guard；stdio 入口不变；`_dispatch`/`_call`/`list_tools`/`call_tool` 逐字未动
- [x] **`main.py` mount**
  - [x] 按文件路径加载 bridge（`importlib.util.spec_from_file_location`）
  - [x] 能力检查（`_MCP_AVAILABLE` + 两个 callable）→ 通过才 `set_base_url` + `app.mount("/mcp", ...)`
  - [x] loopback host：`0.0.0.0`/空 → `127.0.0.1`
  - [x] `try/except`：mcp 缺失/bridge 变更 → DEBUG 跳过，REST 不受影响；成功 INFO
- [x] **测试**（`tests/test_mcp_http_mount.py`）
  - [x] `test_set_base_url_repoints_calling_layer`
  - [x] `test_build_server_lists_all_tools`（in-process `mcp.Client` → 15 工具）
  - [x] `test_build_http_app_is_starlette_with_mcp_route`
  - [x] `pytest.importorskip("mcp")`
- [x] **依赖**（`requirements.txt`）加 `mcp>=2.0`
- [x] **规格/文档**
  - [x] `openspec/specs/mcp-transport.md`（能力真相）
  - [x] `docs/adr/0003-MCP桥内进程化取舍与触发器.md`
  - [x] `AGENTS.md`（版本 + How to run + 契约补充）
  - [x] `docs/INTEGRATION.md`（新增 /mcp 小节）
  - [x] `INSTALL.md` / `INSTALL_CN.md`（验证安装加 /mcp 示例）
  - [x] README 徽标版本（EN + CN）→ 8.1.0
- [x] **版本号三处一致**：VERSION=8.1.0 / README badge / CHANGELOG（新增 v8.1.0 段）

## 验收（带 env 时执行）

- [ ] 带 mcp：`pytest tests/test_mcp_http_mount.py` 绿。
- [ ] 带 DB：起 `main.py`，`POST /mcp initialize` 返回 200 + SSE 含 `"mnemosyne"`；`tools/call` 一轮 round-trip。
- [ ] 无 mcp：`main.py` 起得起来，`/mcp` 无路由、REST 全绿。
- [ ] `pytest tests/` 全绿；隐私扫描零输出。
