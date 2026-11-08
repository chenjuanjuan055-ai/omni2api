# -*- coding: utf-8 -*-
"""给注册机产出的账号自动补 OpenAI refresh_token。

为什么需要
----------
注册机（turb）产出的账号只有 ``access_token``，它自己存的 ``refresh_token``
是**邮箱的 MSA token**，不是 OpenAI 的 ``rt.1....``。直接进主号池会刷新失败，
再被 ``auto_remove_invalid_accounts`` 清掉。

补 rt 走 omni2api 自己的 OAuth 流程：
    POST /api/accounts/oauth/start   -> session_id + authorize_url（omni2api 自己的 PKCE）
    用注册机的协议会话 + 邮箱码走完 auth.openai.com -> platform callback
    POST /api/accounts/oauth/finish   -> 换 token 三件套（含 rt.1.）

实现
----
协议部分复用 ``scripts/gpt_register_grant_rt.py``（已实测跑通），这里用**子进程**调用：
注册机跑在自己的 venv 里（依赖 curl_cffi / playwright 等），主服务环境不保证有，
起子进程最省事也最隔离。
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import threading
import time
from typing import Any

REPO = pathlib.Path(__file__).resolve().parent.parent
TURB_DIR = REPO / "upstreams" / "gpt-register"
TURB_PY = TURB_DIR / ".venv" / "Scripts" / "python.exe"
GRANT_SCRIPT = REPO / "scripts" / "gpt_register_grant_rt.py"

GRANT_TIMEOUT = 420
# 单个账号自动补 rt 的最大尝试次数（超过就放弃，避免死磕）
MAX_ATTEMPTS = 3


class GrantRtError(Exception):
    pass


class GptRegisterGrantRt:
    """注册完成后自动补 OpenAI refresh_token。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._handled: set[str] = set()
        self._results: dict[str, dict[str, Any]] = {}
        self._watcher: threading.Thread | None = None
        self._stop = threading.Event()

    # ---------------- 单个账号 ----------------
    def grant(self, email: str, proxy: str | None = None) -> dict[str, Any]:
        """给一个账号补 rt。返回 {ok, email, detail}。"""
        email = (email or "").strip()
        if not email:
            raise GrantRtError("邮箱为空")
        if not TURB_PY.exists():
            raise GrantRtError(f"找不到注册机运行环境: {TURB_PY}")
        if not GRANT_SCRIPT.exists():
            raise GrantRtError(f"找不到补 rt 脚本: {GRANT_SCRIPT}")

        cmd = [str(TURB_PY), str(GRANT_SCRIPT), email]
        if proxy:
            cmd.append(proxy)
        started = time.time()
        try:
            proc = subprocess.run(
                cmd, cwd=str(REPO), capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=GRANT_TIMEOUT,
            )
        except subprocess.TimeoutExpired as exc:
            return {"ok": False, "email": email, "detail": f"补 rt 超时（{GRANT_TIMEOUT}s）"}
        out = (proc.stdout or "") + (proc.stderr or "")
        ok = proc.returncode == 0
        detail = ""
        for line in reversed(out.splitlines()):
            if line.strip():
                detail = line.strip()[:300]
                break
        if ok:
            for line in out.splitlines():
                if "rt=" in line and ("rt.1." in line or "rt=空" in line):
                    detail = line.strip()[:300]
                    break
        result = {
            "ok": ok,
            "email": email,
            "detail": detail,
            "returncode": proc.returncode,
            "elapsed": round(time.time() - started, 1),
        }
        with self._lock:
            self._results[email] = result
            if ok:
                self._handled.add(email.lower())
        return result

    # ---------------- 待补清单 ----------------
    def pending(self, console: Any) -> list[dict[str, Any]]:
        """列出"注册机已产出、但主号池里还没有 rt.1. 格式 rt"的账号。

        数据源用注册机的**账号列表**（不是任务列表）—— 任务列表只留最近几条，
        早先成功的注册不在里面，但账号一直在。
        """
        out: list[dict[str, Any]] = []
        accounts: list[dict[str, Any]] = []
        try:
            data = console.status()
            if isinstance(data, dict):
                accounts = data.get("accounts") or []
        except Exception:
            accounts = []
        pool_emails = self._pool_emails_with_rt()
        seen: set[str] = set()
        for item in accounts:
            if not isinstance(item, dict):
                continue
            email = str(item.get("email") or "").strip()
            if not email or email.lower() in seen:
                continue
            seen.add(email.lower())
            out.append({
                "email": email,
                "status": "registered",
                "email_source": item.get("email_source"),
                "has_rt": email.lower() in pool_emails,
                "granted": email.lower() in self._handled,
            })
        return out

    @staticmethod
    def _pool_emails_with_rt() -> set[str]:
        """主号池里已经带 OpenAI rt 的邮箱集合（小写）。"""
        found: set[str] = set()
        pool_file = REPO / "data" / "accounts.json"
        if not pool_file.exists():
            return found
        try:
            pool = json.loads(pool_file.read_text(encoding="utf-8"))
        except Exception:
            return found
        items = pool if isinstance(pool, list) else (pool.get("items") or [])
        for item in items or []:
            if not isinstance(item, dict):
                continue
            rt = str(item.get("refresh_token") or "")
            email = str(item.get("email") or "").strip().lower()
            if email and rt.startswith("rt.1."):
                found.add(email)
        return found

    # ---------------- 后台 watcher ----------------
    def start_watcher(self, console: Any) -> None:
        """注册任务跑完后自动补 rt（后台线程，轮询注册任务列表）。"""
        with self._lock:
            if self._watcher and self._watcher.is_alive():
                return
            self._stop.clear()
            self._watcher = threading.Thread(
                target=self._watch_loop, args=(console,), daemon=True,
                name="gpt-register-grant-rt",
            )
            self._watcher.start()
            print("[grant-rt] watcher 线程已启动", flush=True)

    def stop_watcher(self) -> None:
        self._stop.set()

    def _watch_loop(self, console: Any) -> None:
        attempts: dict[str, int] = {}
        while not self._stop.is_set():
            try:
                todo = self.pending(console)
                need = [x for x in todo if not x.get("has_rt")]
                print(f"[grant-rt] 扫描 {len(todo)} 个注册机账号，待补 {len(need)} 个 "
                      f"{[x['email'][:26] for x in need][:4]}", flush=True)
                for item in todo:
                    email = str(item.get("email") or "")
                    if not email or item.get("has_rt") or item.get("granted"):
                        continue
                    key = email.lower()
                    with self._lock:
                        if key in self._handled:
                            continue
                        # 失败重试有上限：有些邮箱（例如已被绑 TOTP）永远补不上，
                        # 不限次数会一直死磕同���个。
                        if attempts.get(key, 0) >= MAX_ATTEMPTS:
                            self._handled.add(key)
                            continue
                        attempts[key] = attempts.get(key, 0) + 1
                    try:
                        print(f"[grant-rt] 开始补 {email}（第 {attempts.get(key, 0)} 次）", flush=True)
                        result = self.grant(email)
                        print(f"[grant-rt] {email} -> ok={result.get('ok')} "
                              f"{str(result.get('detail'))[:160]}", flush=True)
                        if not result.get("ok"):
                            with self._lock:
                                self._handled.discard(key)
                    except Exception as exc:
                        print(f"[grant-rt] {email} 补 rt 异常: {type(exc).__name__}: {exc}", flush=True)
                        with self._lock:
                            self._handled.discard(key)
            except Exception as exc:
                print(f"[grant-rt] watcher 轮次异常: {type(exc).__name__}: {exc}", flush=True)
            self._stop.wait(15)

    def results(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return dict(self._results)


gpt_register_grant_rt = GptRegisterGrantRt()
