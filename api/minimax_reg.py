# -*- coding: utf-8 -*-
"""MiniMax 自动注册的 API 路由（控制台用）。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException

from api.support import require_admin
from services.minimax_reg_console import MinimaxRegError, minimax_reg_console


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/minimax-reg/status")
    async def minimax_reg_status(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return minimax_reg_console.status()

    @router.post("/api/minimax-reg/start")
    async def minimax_reg_start(
            body: dict[str, Any],
            authorization: str | None = Header(default=None)):
        """发起一批自动注册任务（后台跑）。"""
        require_admin(authorization)
        try:
            return minimax_reg_console.start(int(body.get("count") or 1))
        except MinimaxRegError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.post("/api/minimax-reg/stop")
    async def minimax_reg_stop(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return minimax_reg_console.stop()

    @router.get("/api/minimax-reg/tokens")
    async def minimax_reg_tokens(
            limit: int = 50,
            authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"items": minimax_reg_console.tokens(limit), "count": minimax_reg_console.token_count()}

    @router.get("/api/minimax-reg/settings")
    async def minimax_reg_settings(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"settings": minimax_reg_console.public_settings()}

    @router.post("/api/minimax-reg/settings")
    async def minimax_reg_save_settings(
            body: dict[str, Any],
            authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return {"settings": minimax_reg_console.save_settings(body)}
        except MinimaxRegError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    return router
