"""GPT 注册机控制台：对接内嵌的 turb-gpt-free-register WebUI。

turb 自带 Flask WebUI + 76 个 REST 路由，这里做一层薄代理，
让统一控制台能在同一个页面里驱动它（起停任务、看账号、导出 CPA / Sub2API）。

与 Grok 注册页一样：注册机是本项目的一部分，页面上不暴露服务地址。
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

DATA_FILE = Path(__file__).resolve().parents[1] / "data" / "gpt_register.json"
DEFAULT_BASE = "http://127.0.0.1:5000"

# turb 的 WebUI 用一次性的 WEBUI_AUTH_CODE / AUTH_CODE 做访问码。
# 启动时从它的日志里读出来放进这里，前端不需要知道。
AUTH_FILE = Path(__file__).resolve().parents[1] / "data" / "gpt_register_auth.json"


class GptRegisterError(RuntimeError):
    """注册机不可用或返回错误。"""


class GptRegisterConsole:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._auth_code: str = ""

    # ---------- 设置 ----------
    def settings(self) -> dict[str, Any]:
        data: dict[str, Any] = {}
        if DATA_FILE.exists():
            try:
                raw = json.loads(DATA_FILE.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    data = raw
            except Exception:
                data = {}
        return {
            "base_url": str(data.get("base_url") or DEFAULT_BASE).rstrip("/"),
            "auth_code_set": bool(self.auth_code()),
        }

    def save_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        current = self.settings()
        base = str(payload.get("base_url") or current["base_url"]).strip().rstrip("/")
        if not base.startswith(("http://", "https://")):
            raise GptRegisterError("地址必须以 http:// 或 https:// 开头")
        DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        DATA_FILE.write_text(
            json.dumps({"base_url": base}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return self.settings()

    def auth_code(self) -> str:
        if self._auth_code:
            return self._auth_code
        if AUTH_FILE.exists():
            try:
                self._auth_code = json.loads(AUTH_FILE.read_text(encoding="utf-8")).get("code", "")
            except Exception:
                self._auth_code = ""
        return self._auth_code

    def set_auth_code(self, code: str) -> None:
        self._auth_code = (code or "").strip()
        AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
        AUTH_FILE.write_text(
            json.dumps({"code": self._auth_code}, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ---------- 请求 ----------
    def _request(self, path: str, method: str = "GET", payload: Any = None,
                 timeout: int = 60) -> Any:
        base = self.settings()["base_url"]
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"accept": "application/json"}
        code = self.auth_code()
        if code:
            headers["x-auth-code"] = code
            headers["Authorization"] = f"Bearer {code}"
        if data is not None:
            headers["content-type"] = "application/json"
        request = urllib.request.Request(f"{base}{path}", data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8", "replace")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise GptRegisterError(f"HTTP {exc.code}: {detail}") from exc
        except Exception as exc:
            raise GptRegisterError(f"{type(exc).__name__}: {exc}") from exc

    def _try(self, path: str, method: str = "GET", payload: Any = None,
             timeout: int = 60) -> tuple[bool, Any]:
        try:
            return True, self._request(path, method, payload, timeout)
        except Exception as exc:
            return False, str(exc)

    # ---------- 业务 ----------
    def status(self) -> dict[str, Any]:
        ok, data = self._try("/api/accounts")
        if not ok:
            return {"ok": False, "error": data}
        items = data.get("accounts") if isinstance(data, dict) else data
        if not isinstance(items, list):
            items = data.get("items") if isinstance(data, dict) else []
        return {"ok": True, "count": len(items or []), "accounts": (items or [])[:50]}

    def accounts(self, page: int = 1, page_size: int = 20, q: str = "") -> dict[str, Any]:
        query = f"?page={max(1, page)}&pageSize={max(1, min(page_size, 200))}"
        if q:
            query += f"&search={urllib.parse.quote(q)}"
        ok, data = self._try(f"/api/accounts{query}")
        if not ok:
            raise GptRegisterError(str(data))
        return data if isinstance(data, dict) else {"items": data}

    def start_register(self, count: int = 1, workers: int = 1) -> dict[str, Any]:
        """发起一批自动注册任务（由注册机自己执行，控制台只下发参数与看进度）。

        注册成功后由后台 watcher 自动补 OpenAI refresh_token（见 services/gpt_register_grant_rt.py）。
        """
        try:
            n = max(1, min(int(count or 1), 20))
            w = max(1, min(int(workers or 1), 5))
        except (TypeError, ValueError) as exc:
            raise GptRegisterError("数量或并发参数不合法") from exc
        ok, data = self._try("/api/jobs", "POST", {"count": n, "workers": w}, timeout=120)
        if not ok:
            raise GptRegisterError(str(data))
        try:
            from services.gpt_register_grant_rt import gpt_register_grant_rt
            gpt_register_grant_rt.start_watcher(self)
            print("[gpt-register] 凭据补全 watcher 已启动", flush=True)
        except Exception as exc:
            print(f"[gpt-register] 凭据补全 watcher 启动失败: {type(exc).__name__}: {exc}", flush=True)
        return data if isinstance(data, dict) else {"ok": True, "raw": data}

    def register_status(self) -> dict[str, Any]:
        """读取当前注册任务进度。"""
        ok, data = self._try("/api/jobs", "GET", None, timeout=30)
        if not ok:
            raise GptRegisterError(str(data))
        if isinstance(data, dict):
            return data
        return {"jobs": data if isinstance(data, list) else []}

    def codex_status(self) -> dict[str, Any]:
        ok, data = self._try("/api/codex")
        if not ok:
            raise GptRegisterError(str(data))
        return data if isinstance(data, dict) else {"items": data}

    def codex_action(self, action: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if action not in ("retry", "stop", "archive", "reset-export"):
            raise GptRegisterError(f"不支持的操作 {action}")
        ok, data = self._try(f"/api/codex/{action}", "POST", payload or {}, timeout=120)
        if not ok:
            raise GptRegisterError(str(data))
        return data if isinstance(data, dict) else {"ok": True}

    def account_action(self, account_id: int, action: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        allowed = ("probe", "quota", "credit", "secret", "totp-setup", "note", "archive", "delete",
                   "codex-agent")
        if action not in allowed:
            raise GptRegisterError(f"不支持的操作 {action}")
        method = "GET" if action in ("secret", "totp-setup") else "POST"
        path = f"/api/accounts/{account_id}"
        if action in ("probe", "quota", "credit", "codex-agent"):
            path += f"/{action}"
        elif action in ("note", "archive", "delete", "totp-setup"):
            path += f"/{action}"
        ok, data = self._try(path, method, payload or {}, timeout=180)
        if not ok:
            raise GptRegisterError(str(data))
        return data if isinstance(data, dict) else {"ok": True}


gpt_register_console = GptRegisterConsole()
