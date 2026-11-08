#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给内嵌的 turb-gpt-free-register 打补丁：适配 OpenAI 2026-11 的新认证流程。

为什么需要
----------
OpenAI 在 2026-11 改了 auth.openai.com 的注册流程，上游 turb（最后提交 2026-10-24）
的协议实现会卡死：

* 旧端点 ``GET /api/accounts/email-otp/send`` **已下线**。命中 SPA 兜底会返回
  ``200`` 的 HTML 页面 —— 看起来"发码成功"，其实根本没发码。
  现网端点是 ``POST /api/accounts/passwordless/send-otp``（且只接受 ``application/json``）。
* 顺序反了。旧实现是"先 ``POST /api/accounts/user/register`` 建密码账号，再收邮箱码"；
  现网是"**先收邮箱码**，服务端再决定要不要密码（多数情况下免密建号直接给 OAuth 回调）"。
  旧顺序下 ``user/register`` 恒回 ``400 {"code":"invalid_auth_step"}``，与 IP/邮箱无关。

改动
----
1. ``core/openai_auth.py``：新增 ``send_passwordless_otp()``（现网端点）。
2. ``main.py``：把"触发发码 + 收码 + 校验码"提到"创建密码账号"之前，
   并新增"校验码后服务端要求设置密码"分支（老流程作为兼容路径保留）。

幂等：重复执行不会重复打补丁。

用法
----
    python scripts/apply_gpt_register_flow_patch.py
    python scripts/apply_gpt_register_flow_patch.py --check     # 只检查不打
"""
from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent / "upstreams" / "gpt-register"

AUTH_PY = ROOT / "core" / "openai_auth.py"
MAIN_PY = ROOT / "main.py"

SENTINEL_FN = '''def send_passwordless_otp(
    session: BrowserSession,
    sentinel_header: str | None = None,
    so_header: str | None = None,
) -> dict:
    """触发 OpenAI 免密邮箱验证码（2026-11 现网流程）。

    旧端点 ``GET /api/accounts/email-otp/send`` 已下线，命中 SPA 兜底会返回 200 的
    HTML（看起来"成功"但并不会真的发码）。现网端点是
    ``POST /api/accounts/passwordless/send-otp``，只接受 ``application/json``。
    """
    url = "https://auth.openai.com/api/accounts/passwordless/send-otp"
    headers = session.get_auth_headers(referer="https://auth.openai.com/")
    headers["content-type"] = "application/json"
    headers["sec-fetch-site"] = "same-origin"
    if sentinel_header:
        headers["openai-sentinel-token"] = sentinel_header
    if so_header:
        headers["openai-sentinel-so-token"] = so_header
    logger.info("[OTP] 请求触发免密邮箱验证码 (passwordless/send-otp)...")
    resp = session.post(url, headers=headers)
    if resp.status_code >= 400:
        raise RuntimeError(
            f"passwordless/send-otp 失败 status={resp.status_code}: {(resp.text or '')[:300]}"
        )
    try:
        data = resp.json()
    except Exception:
        data = {}
    page = data.get("page") if isinstance(data, dict) else {}
    page = page if isinstance(page, dict) else {}
    logger.info(
        "[OTP] 免密发码成功 page_type=%s mode=%s",
        page.get("type") or "空",
        ((page.get("payload") or {}) or {}).get("email_verification_mode") or "空",
    )
    return data if isinstance(data, dict) else {}


'''

OLD_FLOW_BLOCK = '''        # 步骤5-8: 强制走邮箱+密码注册，不使用 passwordless OTP-only 分支。
        navigate_create_account_password(session, authorize_final_url)
        human_delay("navigate")
        registration_password = generate_registration_password()
        password_sentinel = request_password_sentinel_bundle(session)
        password_sentinel_header, password_so_header = build_sentinel_header(
            session, password_sentinel, "username_password_create"
        )
        human_delay("challenge")
        register_result = register_user(
            session,
            email,
            registration_password,
            password_sentinel_header,
            password_so_header,
        )
        create_acknowledged = True

        # 只读取本次发送之后到达的验证码，避免误取历史邮件。
        otp_after_ts = time.time()
        navigate_email_otp_send(session, register_result.get("continue_url"))
        human_delay("navigate")
'''

NEW_FLOW_BLOCK = '''        # ==================== 阶段3: 邮箱验证码（2026-11 现网顺序）===================
        # 现网顺序已从"先建密码账号再收码"改成"先收邮箱码，再由服务端决定要不要密码"：
        #   authorize -> 落点 /email-verification -> POST /api/accounts/passwordless/send-otp
        #   -> 校验码 -> 直接给 OAuth 回调（免密建号）或要求 /create-account/password。
        # 旧顺序会让 user/register 恒回 400 {"code":"invalid_auth_step"}。
        otp_after_ts = time.time()
        send_passwordless_otp(session)
        human_delay("navigate")
'''

UNKNOWN_OLD = """            if page_type and page_type not in ("about_you", "about-you"):
                if otp_continue_url and "about-you" not in str(otp_continue_url):
                    raise RuntimeError(
                        f"OTP 后续页面类型未知，不应盲目 create_account: "
                        f"page_type={page_type}, resp={validate_result}"
                    )"""

UNKNOWN_NEW = """            if page_type and page_type not in ("about_you", "about-you"):
                if page_type == "mfa_challenge":
                    # 该邮箱在 OpenAI 侧已绑定 TOTP 二次验证：邮箱码已验过（email_verified=True），
                    # 但服务端要求再交一次 TOTP。没有 totp_secret 就只能放弃这个邮箱。
                    raise RuntimeError(
                        f"该邮箱在 OpenAI 侧已绑定 TOTP 二次验证（mfa_challenge），"
                        f"没有 totp_secret 无法继续，请换一个未绑定 MFA 的邮箱。"
                    )
                if otp_continue_url and "about-you" not in str(otp_continue_url):
                    raise RuntimeError(
                        f"OTP 后续页面类型未知，不应盲目 create_account: "
                        f"page_type={page_type}, resp={validate_result}"
                    )"""

BRANCH_ANCHOR = '''        # ==================== 阶段5/6: 完成注册或直接 OAuth 回调 ====================
        otp_continue_text = str(otp_continue_url or "")'''

BRANCH_BLOCK = '''        # ==================== 阶段5/6: 完成注册或直接 OAuth 回调 ====================
        otp_continue_text = str(otp_continue_url or "")
        # 现网也可能在校验码之后要求设置密码（continue_url 指向 /create-account/password）。
        # 这时走"建密码账号 -> 再收一次码 -> OAuth 回调"。
        if otp_continue_text and "/create-account/password" in otp_continue_text:
            logger.info("[注册] OTP 后服务端要求设置密码，走创建密码账号分支")
            navigate_create_account_password(session, otp_continue_text)
            human_delay("navigate")
            registration_password = generate_registration_password()
            password_sentinel = request_password_sentinel_bundle(session)
            password_sentinel_header, password_so_header = build_sentinel_header(
                session, password_sentinel, "username_password_create"
            )
            human_delay("challenge")
            register_result = register_user(
                session,
                email,
                registration_password,
                password_sentinel_header,
                password_so_header,
            )
            create_acknowledged = True
            # 旧端点 /api/accounts/email-otp/send 已下线，改用现网的 passwordless/send-otp
            send_passwordless_otp(session)
            human_delay("navigate")
            otp_after_ts = time.time()
            second_otp = None
            if _email_cfg.USE_EMAIL_SERVICE:
                second_otp = wait_for_otp(email, after_ts=otp_after_ts)
            if second_otp:
                validate_result = validate_email_otp(session, second_otp)
                page = validate_result.get("page") if isinstance(validate_result, dict) else {}
                page = page if isinstance(page, dict) else {}
                otp_continue_url = (
                    validate_result.get("continue_url")
                    or validate_result.get("external_url")
                    or page.get("continue_url")
                    or page.get("external_url")
                    or ""
                )
                otp_continue_text = str(otp_continue_url or "")
                page_type = str(page.get("type") or "")
'''


def patch_auth() -> str:
    t = AUTH_PY.read_text(encoding="utf-8")
    if "def send_passwordless_otp(" in t:
        # 已打过补丁；只确保 content-type 是 json
        if 'headers["content-type"] = "application/json"' not in t:
            t = t.replace(
                '    headers.pop("content-type", None)\n',
                '    headers["content-type"] = "application/json"\n',
            )
            AUTH_PY.write_text(t, encoding="utf-8")
            return "auth: 补 content-type"
        return "auth: 已打过补丁"
    anchor = ('def send_email_otp(session: BrowserSession, referer: str = '
              '"https://auth.openai.com/email-verification") -> None:')
    if anchor not in t:
        raise SystemExit("[auth] 锚点 send_email_otp 没找到，turb 版本可能已变")
    t = t.replace(anchor, SENTINEL_FN + anchor, 1)
    AUTH_PY.write_text(t, encoding="utf-8")
    return "auth: 新增 send_passwordless_otp()"


def patch_main() -> str:
    m = MAIN_PY.read_text(encoding="utf-8")
    if "send_passwordless_otp(session)" in m and OLD_FLOW_BLOCK not in m:
        return "main: 已打过补丁"
    if OLD_FLOW_BLOCK in m:
        m = m.replace(OLD_FLOW_BLOCK, NEW_FLOW_BLOCK, 1)
        changed = "main: 重排流程"
    else:
        changed = "main: 未找到旧流程块（可能已改）"
    if UNKNOWN_OLD in m:
        m = m.replace(UNKNOWN_OLD, UNKNOWN_NEW, 1)
        changed += " + mfa分支"
    if BRANCH_ANCHOR in m and "/create-account/password\" in otp_continue_text" not in m:
        m = m.replace(BRANCH_ANCHOR, BRANCH_BLOCK, 1)
        changed += " + 新增密码分支"
    if "send_passwordless_otp," not in m:
        old_imp = "    follow_authorize,\n    request_sentinel_token,"
        new_imp = "    follow_authorize,\n    request_sentinel_token,\n    send_passwordless_otp,"
        if old_imp not in m:
            raise SystemExit("[main] import 锚点没找到")
        m = m.replace(old_imp, new_imp, 1)
        changed += " + 导入"
    MAIN_PY.write_text(m, encoding="utf-8")
    return changed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只检查是否已打补丁")
    args = ap.parse_args()

    if not ROOT.exists():
        raise SystemExit(f"找不到 {ROOT}")
    a = AUTH_PY.read_text(encoding="utf-8")
    m = MAIN_PY.read_text(encoding="utf-8")
    already = ("def send_passwordless_otp(" in a) and ("send_passwordless_otp(session)" in m)
    if args.check:
        print("已打补丁" if already else "未打补丁")
        return 0 if already else 1
    if already:
        print("已经是打过补丁的版本，跳过")
        return 0
    print(patch_auth())
    print(patch_main())
    import ast
    ast.parse(AUTH_PY.read_text(encoding="utf-8"))
    ast.parse(MAIN_PY.read_text(encoding="utf-8"))
    print("语法检查通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
