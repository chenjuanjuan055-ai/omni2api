# OpenAI 注册流程变更适配（2026-11）

> 结论先行：上游 `turb-gpt-free-register`（最后提交 2026-10-24）的协议注册在 OpenAI 改版后
> **必然失败**，失败点与出口 IP、邮箱、浏览器指纹都无关。本文记录现网真实流程、失败根因、
> 本项目的适配方式，以及出口 IP 与邮箱池两个前置条件的判定标准。

## 一、现网真实流程（2026-11-08 实测抓包）

用真实 Chrome 跑一遍 `chatgpt.com/auth/login` 抓到完整链路：

| 步 | 请求 | 说明 |
|---|---|---|
| 1 | `POST https://chatgpt.com/api/auth/signin/openai?prompt=login&screen_hint=login_or_signup&auth_session_logging_id=…&login_hint=<email>`<br>body: `csrfToken=…&callbackUrl=/&json=true` | 返回 authorize URL |
| 2 | `GET https://auth.openai.com/oauth/authorize?…` | 落点 `/email-verification`（新邮箱）或 `/log-in/password`（已有带密码的账号） |
| 3 | `POST https://auth.openai.com/api/accounts/passwordless/send-otp`<br>`Content-Type: application/json`，带 `openai-sentinel-token` | 触发发码。响应体含 `page.type=email_otp_verification`、`email_verification_mode=passwordless_login`、`login_methods=[passwordless_otp,(password)]` |
| 4 | `POST https://auth.openai.com/api/accounts/email-otp/validate`<br>body `{"code":"123456"}` | 校验码。成功时 `continue_url` 直接是 `https://chatgpt.com/api/auth/callback/openai?code=…` |
| 5 | `GET /api/auth/callback/openai?code=…` → 302 | 换 token，落地 ChatGPT 首页 |

**关键变化：不再有"先填密码建号"这一步。** 邮箱码一验，服务端直接建号/登录并给 OAuth 回调。

## 二、上游为什么必然失败

### 1. 发码端点下线了

上游用的是 `GET https://auth.openai.com/api/accounts/email-otp/send`。
该端点已不存在，命中 SPA 兜底会返回 **200 + 一整页 HTML** —— 代码只判 status code，
于是"看起来发码成功"，实际**根本没发码**，只能干等超时。

正确端点是 `POST /api/accounts/passwordless/send-otp`，且**只接受 `application/json`**
（沿用默认的 `application/x-www-form-urlencoded` 会回
`400 Unsupported content type: 'application/x-www-form-urlencoded'`）。

### 2. 顺序反了

上游是「先 `POST /api/accounts/user/register` 建密码账号 → 再收邮箱码」。
现网是「先收邮箱码 → 服务端再决定要不要密码」。

旧顺序下 `user/register` 恒回：

```json
{"error":{"message":"Invalid authorization step.","type":"invalid_request_error",
          "code":"invalid_auth_step",
          "redirect_uri":"https://chatgpt.com/auth/login_with?callback_path=/"}}
```

实测：**3 个不同出口 IP × 3 个不同邮箱，全部在同一步返回同一个错误码**，
与 IP 信誉、邮箱状态、指纹都无关 ⇒ 纯流程问题。

### 3. 附带一个坑：`screen_hint` 改不动

前端 JS（`https://auth-cdn.oaistatic.com/assets/*.js`）里注册分支是靠

```js
t.searchParams.set("screen_hint","signup");
t.searchParams.set("callback_path","/");
```

选中的。但**只在 authorize URL 上改 `screen_hint` 无效** —— 服务端会话在
`POST /api/auth/signin/openai` 那一步就定型了。真正能区分"新邮箱 / 已有账号"的是**落点**：

* 落点 `/email-verification` → 新邮箱，会发码，走免密建号
* 落点 `/log-in/password` → 该邮箱已有带密码的账号，服务端要密码，**不会发码**

## 三、本项目的适配

`upstreams/gpt-register/` 是 vendored 副本，重新同步上游会丢补丁，所以补丁写成可重复执行的脚本：

```bash
python scripts/apply_gpt_register_flow_patch.py          # 打补丁（幂等）
python scripts/apply_gpt_register_flow_patch.py --check  # 只检查
```

改动内容：

1. `core/openai_auth.py` 新增 `send_passwordless_otp()`
   （`POST /api/accounts/passwordless/send-otp`，`Content-Type: application/json`，可带 sentinel 头）。
2. `main.py` 把「触发发码 → 收码 → 校验码」提到「创建密码账号」之前，
   并新增「校验码后服务端要求设置密码」的兼容分支（老流程保留）。

上游本来就有一条「OTP 后直接走 OAuth 回调」的分支（`direct_oauth_after_otp`），
新顺序下正好命中它，不需要额外写建号逻辑。

## 四、两个前置条件（比代码更重要）

### 1. 出口 IP 必须过 `auth.openai.com` 的 Cloudflare 校验

`GET https://chatgpt.com/cdn-cgi/trace` 返回 200 **不代表能注册**：
Cloudflare WARP / AWS / 机房 IP 全都能过这个。

真正的判据：

```bash
GET https://auth.openai.com/oauth/authorize     # 缺参数即可
  400 / 302  -> CF 已放行（缺参数只是业务错误）
  403        -> CF 按出口 IP 信誉拦截
```

实测：Cloudflare WARP 出口在 `auth.openai.com` **全部 403**；能过的是小 ASΝ 的
PEG TECH / Hurricane Electric / HostPapa / 1GSERVERS / Akile 等。

多出口生成与探测见 `scripts/outlets_gen_from_v2rayn.py` / `scripts/outlets_probe.py`。

### 2. 邮箱必须是"从没用过 OpenAI 的处女邮箱"

池里的邮箱被之前的农场批量用过，服务端会走登录分支：

* 校验码回 `403 {"code":"account_deactivated"}`（账号已删除/停用）
* 发出的邮件标题是 **"Your temporary ChatGPT login code"（登录码）**，
  而不是新账号该有的"Verify your email address" —— 这是"服务端把它当已有账号"的铁证。

识别方法：按**发件人域名**（`noreply@tm.openai.com` / `noreply@email.openai.com`）筛，
不要只看标题关键词（会和其他服务的验证码邮件混淆）。

## 五、turb 取件模式

`OUTLOOK_FETCH_MODE=auto` 会先打 `mail.chatai.codes` 远端，
**只有远端返回 402/DEPLOYMENT_DISABLED 才回退 Microsoft Graph/IMAP 直连**；
远端被 Cloudflare 挡（403 "Just a moment…"）时会一直空转到超时。

注册前必须设：

```
OUTLOOK_FETCH_MODE=direct
```

另外注意邮箱池的**段序**：`email----password----refreshToken----clientId`
（node-reg 产物）与 turb 期望的 `email----password----clientId----refreshToken` 相反。
搞反了 MSA 会回 `AADSTS9002313 "Invalid request. Request is malformed or invalid."`，
不是 `invalid_grant`，很容易误判成死号。

## 六、入口

控制台：`http://127.0.0.1:8000/gpt/register` → 「自动注册」
（内嵌 turb 的 WebUI，`x-auth-code: omni2api-local`）。
