"""v7.8.3 — the service's listen port/address are determined by environment variables
(fixes a hardcoded value in main.py's entry point)

Two-layer contract:
  (1) `config.PORT` / `config.HOST` read `MNEMOSYNE_PORT` / `MNEMOSYNE_HOST` (verified via a
      subprocess so this process isn't polluted)
  (2) the service entry point `main._run_server()` **must use** these two values — this is
      the actual fix in this change: the entry point used to hardcode
      `host="127.0.0.1", port=8010`, silently ignoring the env vars (no error, just no effect).
      Case (2) was confirmed to fail on the old code (hardcoded gave captured["port"] == 8010 != 9123).
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _config_value(expr: str, env_override: dict) -> str:
    env = dict(os.environ)
    for k in ("MNEMOSYNE_PORT", "MNEMOSYNE_HOST"):
        env.pop(k, None)
    env.update(env_override)
    out = subprocess.run(
        [sys.executable, "-c", f"import config; print({expr})"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


class TestConfigReadsEnv:
    def test_default_port_and_host(self):
        assert _config_value("config.PORT", {}) == "8010"
        assert _config_value("config.HOST", {}) == "127.0.0.1"

    def test_env_override_is_honored(self):
        assert _config_value("config.PORT", {"MNEMOSYNE_PORT": "9123"}) == "9123"
        assert _config_value("config.HOST", {"MNEMOSYNE_HOST": "0.0.0.0"}) == "0.0.0.0"


class TestServerEntryUsesConfig:
    """Entry-point wiring contract — this case was confirmed to fail when the port was hardcoded."""

    def test_run_server_passes_config_host_and_port(self, monkeypatch):
        import uvicorn
        import main

        captured = {}
        monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: captured.update(kw))
        monkeypatch.setattr(main, "PORT", 9123, raising=False)
        monkeypatch.setattr(main, "HOST", "0.0.0.0", raising=False)

        main._run_server()

        assert captured.get("port") == 9123, f"entry point didn't use config.PORT: {captured}"
        assert captured.get("host") == "0.0.0.0", f"entry point didn't use config.HOST: {captured}"
        assert captured.get("app") == "main:app" or captured.get("app") is None
