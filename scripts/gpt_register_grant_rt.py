#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给已注册的 ChatGPT 账号补真正的 OpenAI refresh_token。

为什么需要
----------
turb 注册机导出的 ``refresh_token`` 其实是**邮箱的 MSA token**（``M.C5xx_BAY...``，
Outlook 来源才有），不是 OpenAI 的 refresh_token（``rt.1....``）。
把 MSA token 填进 omni2api 主号池，刷新时会直接失败；又因为
``auto_remove_invalid_accounts=true``，账号还会被自动清理掉。

补 rt 的正规路径就在 omni2api 自己的 OAuth 流程里：

    POST /api/accounts/oauth/start   -> session_id + authorize_url（omni2api 自己的 PKCE）
    用 turb 的 BrowserSession + 邮箱码走完 auth.openai.com -> 落到 callback URL
    POST /api/accounts/oauth/finish   -> 换 token 三件套（含 rt.1. 格式的 refresh_token）

注意 omni2api 的 authorize 端点是 ``/api/accounts/authorize``（不是 ``/oauth/authorize``），
``redirect_uri`` 是 ``https://platform.openai.com/auth/callback``，所以走完流程后
``continue_url`` 直接就是 platform 的 callback，可原样喂回 ``oauth/finish``。

用法
----
    python scripts/gpt_register_grant_rt.py <email> [proxy]
"""
from __future__ import annotations

import json
import pathlib
import sys
import time
import urllib.parse

import requests

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "upstreams" / "gpt-register"))

from core.session import BrowserSession  # noqa: E402
from core.chatgpt_auth import get_providers, get_csrf_token  # noqa: E402
from core.openai_auth import (  # noqa: E402
    send_passwordless_otp, validate_email_otp, request_sentinel_token,
    request_password_sentinel_bundle, build_sentinel_header,
)
from core.email_provider import wait_for_otp  # noqa: E402

OMNI = "http://127.0.0.1:8000"
AUTH = {"Authorization": "Bearer chatgpt2api"}


def api(method: str, path: str, **kw):
    s = requests.Session()
    s.trust_env = False
    return s.request(method, OMNI + path, headers=AUTH, timeout=kw.pop("timeout", 120), **kw)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    email = sys.argv[1]
    proxy = sys.argv[2] if len(sys.argv) > 2 else None

    r = api("POST", "/api/accounts/oauth/start", json={"email_hint": email})
    print("[oauth/start]", r.status_code)
    info = r.json()
    session_id = info.get("session_id")
    authorize_url = info.get("authorize_url") or info.get("url")
    print("  session_id:", session_id)

    s = BrowserSession(proxy=proxy) if proxy else BrowserSession()
    get_providers(s)
    get_csrf_token(s)
    hdr = s.get_auth_navigate_headers(referer="https://platform.openai.com/")
    resp = s.get(authorize_url, headers=hdr, allow_redirects=True)
    final = str(getattr(resp, "url", ""))
    print("[1] authorize 落点:", final)

    if "email-verification" not in final:
        print("  该邮箱不是免密注册分支（落点非 /email-verification），"
              "需要走密码登录分支，本脚本不处理。")
        return 3

    ts = time.time()
    send_passwordless_otp(s)
    print("[2] 已触发发码")
    otp = wait_for_otp(email, after_ts=ts)
    print("[3] 收到 OTP:", otp)

    bundle = request_password_sentinel_bundle(s)
    tok = request_sentinel_token(s, "authorize_continue")
    sent, so = build_sentinel_header(s, tok, "authorize_continue")
    vh = s.get_auth_headers(referer="https://auth.openai.com/email-verification")
    vh["openai-sentinel-token"] = sent
    if so:
        vh["openai-sentinel-so-token"] = so
    v = s.post("https://auth.openai.com/api/accounts/email-otp/validate",
               headers=vh, data=json.dumps({"code": otp}))
    print("[4] validate:", v.status_code)
    try:
        vj = v.json()
    except Exception:
        vj = {}
    page = vj.get("page") or {}
    print("    page.type:", page.get("type"), " mode:",
          ((vj.get("oai-client-auth-session") or {}).get("email_verification_mode") or ""))
    cu = vj.get("continue_url") or vj.get("external_url") or ""
    if not cu:
        print("  没拿到 continue_url")
        return 4

    h2 = s.get_auth_navigate_headers(referer="https://auth.openai.com/email-verification")
    if cu.startswith("https://chatgpt.com"):
        h2 = s.get_chatgpt_navigate_headers(referer="https://auth.openai.com/email-verification")
    r2 = s.get(cu, headers=h2, allow_redirects=True)
    final = str(getattr(r2, "url", ""))
    print("[5] callback:", final[:120])

    if "code=" not in final:
        print("  未拿到 code")
        return 5

    r3 = api("POST", "/api/accounts/oauth/finish",
             json={"session_id": session_id, "callback": final})
    print("[6] oauth/finish ->", r3.status_code, r3.text[:200])

    items = api("GET", "/api/accounts").json().get("items") or []
    for a in items:
        if str(a.get("email", "")).lower() == email.lower():
            rt = str(a.get("refresh_token") or "")
            print(f"  {a.get('email')}  rt={(rt[:30] + '…') if rt else '空'}  "
                  f"status={a.get('status')} quota={a.get('quota')}")
            return 0 if rt.startswith("rt.1.") else 6
    return 7


if __name__ == "__main__":
    raise SystemExit(main())
