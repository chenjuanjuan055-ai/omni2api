"""GPT 注册机 API：统一控制台里驱动内嵌的 turb-gpt-free-register。"""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from starlette.concurrency import run_in_threadpool

from api.support import require_admin
from services.gpt_register_console import GptRegisterError, gpt_register_console


def _guard(error: Exception) -> HTTPException:
    return HTTPException(status_code=502, detail={"error": str(error)[:300]})


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/gpt-register/settings")
    async def get_settings(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"settings": gpt_register_console.settings()}

    @router.post("/api/gpt-register/settings")
    async def save_settings(body: dict, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return {"settings": gpt_register_console.save_settings(body)}
        except Exception as exc:
            raise _guard(exc) from exc

    @router.post("/api/gpt-register/auth-code")
    async def set_auth_code(body: dict, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        gpt_register_console.set_auth_code(str(body.get("code") or ""))
        return {"ok": True}

    @router.get("/api/gpt-register/status")
    async def status(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return await run_in_threadpool(gpt_register_console.status)

    @router.get("/api/gpt-register/accounts")
    async def accounts(page: int = 1, page_size: int = 20, q: str = "",
                       authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return await run_in_threadpool(gpt_register_console.accounts, page, page_size, q)
        except Exception as exc:
            raise _guard(exc) from exc

    @router.post("/api/gpt-register/register")
    async def start_register(
            body: dict,
            authorization: str | None = Header(default=None)):
        """发起一批自动注册任务。"""
        require_admin(authorization)
        try:
            return await run_in_threadpool(
                gpt_register_console.start_register,
                int(body.get("count") or 1),
                int(body.get("workers") or 1),
            )
        except GptRegisterError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.post("/api/gpt-register/grant-rt")
    async def grant_rt(
            body: dict,
            authorization: str | None = Header(default=None)):
        """给指定账号补 OpenAI refresh_token（邮箱码 -> OAuth 回调 -> 换 token）。"""
        require_admin(authorization)
        email = str(body.get("email") or "").strip()
        if not email:
            raise HTTPException(status_code=400, detail={"error": "邮箱不能为空"})
        proxy = str(body.get("proxy") or "").strip() or None
        try:
            from services.gpt_register_grant_rt import gpt_register_grant_rt
            return await run_in_threadpool(gpt_register_grant_rt.grant, email, proxy)
        except Exception as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.get("/api/gpt-register/grant-rt/pending")
    async def grant_rt_pending(authorization: str | None = Header(default=None)):
        """列出已注册但还没拿到 OpenAI rt 的账号。"""
        require_admin(authorization)
        try:
            from services.gpt_register_grant_rt import gpt_register_grant_rt
            items = await run_in_threadpool(gpt_register_grant_rt.pending, gpt_register_console)
            return {"items": items, "results": gpt_register_grant_rt.results()}
        except Exception as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.get("/api/gpt-register/register/status")
    async def register_status(authorization: str | None = Header(default=None)):
        """读取自动注册任务进度。"""
        require_admin(authorization)
        try:
            return await run_in_threadpool(gpt_register_console.register_status)
        except GptRegisterError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.get("/api/gpt-register/codex")
    async def codex_status(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return await run_in_threadpool(gpt_register_console.codex_status)
        except Exception as exc:
            raise _guard(exc) from exc

    @router.post("/api/gpt-register/codex/{action}")
    async def codex_action(action: str, body: dict,
                           authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return await run_in_threadpool(gpt_register_console.codex_action, action, body)
        except Exception as exc:
            raise _guard(exc) from exc

    @router.post("/api/gpt-register/accounts/{account_id}/{action}")
    async def account_action(account_id: int, action: str, body: dict,
                             authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return await run_in_threadpool(
                gpt_register_console.account_action, account_id, action, body
            )
        except Exception as exc:
            raise _guard(exc) from exc

    return router
