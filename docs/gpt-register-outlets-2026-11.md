# GPT 注册的出口与邮箱供给（2026-11）

> 注册能不能成，取决于三件事：**出口 IP 过不过 Cloudflare 信誉校验**、**邮箱是不是处女**、
> **流程跟没跟现网版本对齐**。前两件由本机两个现成服务供给，第三件见
> [`gpt-register-flow-2026-11.md`](./gpt-register-flow-2026-11.md)。

## 一、两个供给端

| 组件 | 端口 | 目录 | 作用 |
|---|---|---|---|
| 站点注册控制台 | 19879 | `D:\xi\site-console` | VVCloud 住宅IP机场的注册机，账号自带订阅链接 |
| 邮箱验证码管理工具 | 19878 | `D:\xi\msa-mail-tool` | 21268 张邮箱卡，含 Outlook 凭据与收信 API |

### 住宅出口从哪来

VVCloud 是**住宅 IP 机场**，注册账号即送 24h / 1GB 试用。控制台里每个账号都有
`subscribe_url`（形如 `https://g.vvud.us/s/<token>`），订阅里带
`专线A1-美国4-家宽IP银行`、`专线A1-美国8-双ISP家宽IP`、`专线A1-美国5-家宽ChatGPT…`
这类**落地在美国家宽**的节点。

```bash
# 从控制台拉订阅 -> 生成"一节点一本地端口"的 mihomo 多出口
python scripts/outlets_from_vvcloud.py --console http://127.0.0.1:19879 \
    --out <目录> --base-port 28100
# 探测：出口 IP / 归属 / 能否打 chatgpt.com / 能否过 auth.openai.com 的 CF 校验
python scripts/outlets_probe.py --dir <目录>
```

实测 23 个节点里 21 活、19 能打 chatgpt.com、**17 过 auth.openai.com 的 CF 校验**，
其中 5 条美国家宽全过。

### 邮箱从哪挑

```bash
# msa-mail-tool 的卡片接口只给状态，凭据在本地 cards.txt
# 段序：email----password----refreshToken----clientId   （注意与 turb 期望相反）
```

筛选条件（任一不满足就别用）：

1. `@outlook.com` / `@hotmail.com`
2. 卡片自身 `used == false`（`used.json` / `disabled.json` 是全局的，别拿它当过滤条件）
3. 凭据能换到 MSA token 且能读到收件箱
4. **收件箱里没有 OpenAI 发件人（`noreply@tm.openai.com`）的邮件** ⇒ 服务端会当新邮箱
5. **服务端没有 TOTP factor** ⇒ 否则校验码后落到 `mfa_challenge` 页面拿不到 token

实测 354 张 Outlook 卡里 328 张凭据可用、只有 5 张是 OpenAI 处女。

## 二、四个踩过的坑

### 1. 节点密码按账号绑定

同一个 `df.668fx.com:56512` 端口，**不同订阅账号下发的密码不同**：

```
amandaholtz13699 的订阅  -> df.668fx.com:56512  密码 266d3096-0f91-49e5-…
johngentrys53036f 的订阅 -> df.668fx.com:56512  密码 ca08129c-3ca4-4a35-…
```

拿 A 账号的订阅去连 B 账号的节点 = 握手直接被 RST（`ConnectionResetError 10054`），
表现和"节点过期"一模一样，很容易误判。**必须同一个账号的订阅生成配置。**

### 2. 控制台自己的代理配置没建 GLOBAL 组

`site-console` 生成的 mihomo 配置是 `mode: global`，但没有 `proxy-groups`，
于是 GLOBAL 组为空 → **流量全部直连**，出口变成本机 IP（实测是中国移动重庆段）。

它自己的"节点延迟探测"走的是 mihomo API 测节点，不经转发，所以能测出 200~2500ms 的延迟，
**看起来一切正常，实际转发是直连**。自己生成配置时给每个 listener 显式绑 `proxy`。

### 3. 试用订阅里的信息节点不是节点

订阅里混着 `✅付费后可用全部线路`、`剩余流量：1020 MB`、`续费网址:…` 这类说明行，
不过滤会生成一堆连不上的 proxy。

### 3.5 已有现成实例可以直接借用，别重复生成

`D:\xi\tmp_9router\vv-proxy\` 里 9router 已经跑着一个 **VVCloud 多出口 mihomo**
（mixed-port 8099，listeners 8099-8253，7 个账号 × 22 节点，每条节点一个本地端口，
名字前缀是账号缩写，如 `johngent-专线A1-美国8-双ISP家宽IP`）。

直接借它的端口当 ChatGPT 注册出口即可，**不要另起实例**（另起会把机场的连接配额吃掉，
把唯一能用的窗口也挤掉）。实测可用：

| 端口 | 节点 | 出口 IP |
|---|---|---|
| 8149 | juliawil-美国8-双ISP家宽IP | 38.175.211.10 (US, Cogent) |
| 8169 | amandaho-美国4-家宽IP银行 | 38.76.151.53 (US, Cogent) |
| 8191 | christin-美国4-家宽IP银行 | 38.76.151.53 (US, Cogent) |
| 8193 | christin-美国8-双ISP家宽IP | 38.175.211.10 (US, Cogent) |
| 8214 | johngent-美国5-家宽ChatGPT | 216.36.87.151 (US, GTT) |
| 8215 | johngent-美国8-双ISP家宽IP | 38.175.211.10 (US, Cogent) |
| 8237 | laurajoh-美国8-双ISP家宽IP | 38.175.211.10 (US, Cogent) |

### 4. 试用节点的有效窗口只有几十秒 —— **不要先探测再注册**

实测：`专线A1-美国4-家宽IP` 在探测时能拿到出口 IP（38.76.151.53 / US / Cogent），
紧接着再跑注册就全部 `SSL_connect: Connection closed abruptly`。
探一次活，就把那个窗口用掉了。

⇒ **直接拿节点去注册**，让 turb 自己的重试机制顶（预检有 3 次退避重试），
   不要在前面加"探活 + 选优"这一步。要选优就在**一次注册尝试内部**轮换端口。

### 5. 试用账号里只有一部分的节点密码真正可用

7 个 VVCloud 试用账号，同一条 `专线A1-美国4-家宽IP`：
用 johngentrys53036f 的订阅能连，用其余 6 个的订阅全部 RST。
⇒ 机场侧对"账号 + 节点密码"的组合有校验，不是"注册成功就都能用"。
   真正能用的那个账号，一次只放行一条连接。

### 6. 判"能不能注册"不能用 `cdn-cgi/trace`

Cloudflare WARP / AWS / 机房 IP 全部能过 `GET https://chatgpt.com/cdn-cgi/trace`。
真正的判据在 auth 侧：

```bash
GET https://auth.openai.com/oauth/authorize    # 缺参数即可
  400 / 302  -> CF 已放行（缺参数只是业务错误）
  403        -> CF 按出口 IP 信誉拦截
```

## 三、注册链路现状

```
msa-mail-tool(19878)  ──挑 4 个处女 Outlook──┐
                                              ├─→ turb 注册机(5000) ─→ omni2api 主池
VVCloud 订阅(19879) ──17 条过 CF 的多出口─────┘
```

turb 侧需要的配置（`/api/config`，也可用控制台改）：

```
REGISTRATION_DRIVER=protocol
USE_EMAIL_SERVICE=True
EMAIL_SOURCE=outlook
OUTLOOK_FETCH_MODE=graph          # IMAP 通道会被 Microsoft 限流，Graph 稳
PROXY_POOL=http://127.0.0.1:28104
             http://127.0.0.1:28106
             ...
PROXY_POOL_UPSTREAM_PROXY=        # 必须清空，否则会串上游
```

### turb 的两个静默坑

1. **`--continue-on-fail` 不会换邮箱**：失败后邮箱被 release 回 `available`，
   下一轮又按最小 id 挑中同一个 ⇒ 死循环刷同一个号。
   要换号得手动 `core.db.release_outlook(email, status="failed", note=...)`，
   或者直接调 `main.run_registration(email=..., proxy=...)` 自己指定。
2. **IMAP 通道会被 Microsoft 限流**：批量读信时大量
   `User is authenticated but not connected.`（这个错误只准判死号）。
   同一时刻 REST 直连每次都稳定拿到 20 封 ⇒ 用 `OUTLOOK_FETCH_MODE=graph`。

## 四、判定邮箱是否处女：看服务端自己怎么说

比翻收件箱更准的判据是 `email-otp/validate` 响应里的
`oai-client-auth-session.email_verification_mode`：

| 值 | 含义 | 后续页面 |
|---|---|---|
| `passwordless_signup` | **新邮箱，服务端在给你建号** | `about_you` → 建号完成 |
| `passwordless_login` | 该邮箱已有账号，服务端在给你登录 | `mfa_challenge` / 登录页 |

`passwordless_signup` 落到 `about_you` ⇒ 注册成功；`passwordless_login` 落到
`mfa_challenge` ⇒ 该邮箱被人开过 TOTP，弃用。

实测：`LutkinsSmolnicky1962@outlook.com` 走 `passwordless_signup` → `about_you`
→ 建号成功（plan=free，带 access_token + refresh_token），导入主号池后真实对话验证通过。

## 四点五、IMAP 邮箱池可行，但**域名决定成败**

msa-mail-tool 里除 Outlook 外的 20914 张卡都是 **IMAP 类型**（自建域名 / 政府 / 教育 /
一次性邮箱），turb 原生支持（`EMAIL_SOURCE="imap"` + `core/imap_mail_client.py`）。
抽样 40 张有 22 个能登录收信（55%）。

真正跑一次注册的对照结果：

| 邮箱 | 域名 | OpenAI 发码 | 结果 |
|---|---|---|---|
| `a.bintan.s930@mail.go.id` | 印尼 mail.go.id | ✅ 收到 385796 | **注册成功**（`passwordless_signup` → `about_you`） |
| `1032079542@aliyun.com` | 中国 aliyun | ❌ 27 轮 × 20 封全是旧信 | 等码超时 |

⇒ **aliyun.com 这类中国邮箱域名 OpenAI 不发验证码**；政府 / 教育 / 印尼等自建域名可以。
挑卡时按域名筛，别全池盲试。

## 四点六、⚠ turb 导出的 `refresh_token` 不是 OpenAI 的

turb 保存的 `refresh_token` 字段里装的是**邮箱的 MSA token**（`M.C5xx_BAY...`，
只有 Outlook 来源才有），**不是** OpenAI 的 refresh_token（`rt.1....`）。
直接填进主号池 ⇒ 刷新失败 ⇒ 又因为 `auto_remove_invalid_accounts=true`，**账号会被自动删掉**
（实测 LutkinsSmolnicky 那个号就是这么没的）。

补 rt 的正规路径就在 omni2api 自己的 OAuth 流程里：

```
POST /api/accounts/oauth/start   -> session_id + authorize_url（omni2api 自己的 PKCE）
   ↓ 用 turb 的 BrowserSession + 邮箱码走完 auth.openai.com
POST /api/accounts/oauth/finish   -> 换 token 三件套（含 rt.1. 格式）
```

一键脚本：

```bash
python scripts/gpt_register_grant_rt.py <email> [http://127.0.0.1:8215]
```

要点：
* omni2api 的 authorize 端点是 **`/api/accounts/authorize`**（不是 `/oauth/authorize`），
  `redirect_uri` 是 `https://platform.openai.com/auth/callback`，
  所以走完流程 `continue_url` 直接就是 platform 的 callback，可原样喂回 `oauth/finish`。
* sentinel flow 用 **`authorize_continue`**（需要 turnstile + so + pow 三件）。
* 落点不是 `/email-verification` 的（= 该邮箱已有带密码账号）要另走密码登录分支。

实测：`a.bintan.s930@mail.go.id` 补完拿到 `rt.1.AADsSv3_QxbvLNIlLRvrRGp5y...`，
进主号池后真实对话通过（quota=25）。

## 四点六点五、控制台现在会自动补长期凭据

注册机产出的号只有 access_token，必须再走一遍 OAuth 换到 `rt.1.` 才算"可长期使用"。
这件事已经接进控制台，点一下发起注册就自动完成：

```
控制台「发起注册」
   -> 注册机建号（拿到 access_token）
   -> 后台 watcher 发现"这个账号在主号池里还没有 rt.1. 格式的凭据"
   -> 自动走 omni2api 的 oauth/start + oauth/finish 补 rt
   -> 账号带长期凭据进主号池
```

涉及的三块：

| 位置 | 作用 |
|---|---|
| `services/gpt_register_grant_rt.py` | 自动补 rt 服务：扫注册机账号列表 → 找出缺 rt 的 → 调补 rt 脚本 → 落盘 |
| `scripts/gpt_register_grant_rt.py` | 协议实现（子进程跑，用注册机自己的 venv） |
| `api/gpt_register.py` | `POST /api/gpt-register/grant-rt`（手动补）、`GET /api/gpt-register/grant-rt/pending`（待补清单） |

控制台页面有「凭据补全」卡片：列出缺凭据的账号，每行一个「补全」按钮。

几个要注意的点：
* **判定源用注册机的账号列表**（不是任务列表）—— 任务列表只留最近几条，
  早先成功注册的账号不在里面，但账号一直在。
* **代理要先探活**：试用节点的有效窗口只有几十秒，随机挑很容易挑到已经挂的出口，
  所以挑之前挨个探一遍 `auth.openai.com`。
* **失败重试有上限**（默认 3 次）：有些邮箱（例如已被绑 TOTP）永远补不上，
  不限次数会一直死磕同一个。
* `_handled` 集合的 key 要统一用小写：邮箱在 token 里是**首字母大写**的，
  注册机账号表里也是大写，主号池里是小写。

## 四点七、控制台已能直接发起注册

控制台的 **GPT 自动注册** 页（`/gpt/register`）现在能直接发起注册任务、看实时进度：

```
POST /api/gpt-register/register          {"count": N, "workers": M}
GET  /api/gpt-register/register/status   -> {"jobs": [{email, display_status, error, ...}]}
```

链路：控制台 → `services/gpt_register_console.py` → 注册机的 `/api/jobs`。

## 四点八、账号池按 access_token 建 key 的坑（已修）

账号池 `self._accounts` 以 **access_token 为 key**。同一账号重新登录会换到新的
access_token，于是旧条目（往往带着已失效的 refresh_token）留在池里，
表现成同一邮箱出现两条，其中一条刷新必失败，最后被 `auto_remove_invalid_accounts`
当成无效账号清掉 —— 看起来像"号莫名消失了"。

修法两处：
1. `services/account_service.py`：写入前按 email 去重，同邮箱的旧条目被新 token 顶替。
2. `api/accounts.py` 的 `oauth/finish`：payload 补上 email / user_id / plan
   （email 在 access_token 的 `https://api.openai.com/profile.email`，
   或 id_token 顶层 `email`；注意邮箱是**首字母大写**的，比对时要 `.lower()`）。

## 五、入口

控制台：`http://127.0.0.1:8000/gpt/register` → 「自动注册」
（内嵌 turb 的 WebUI，授权头 `x-auth-code: omni2api-local`）。
