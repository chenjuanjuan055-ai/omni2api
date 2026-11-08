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

### 4. 判"能不能注册"不能用 `cdn-cgi/trace`

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

## 四、入口

控制台：`http://127.0.0.1:8000/gpt/register` → 「自动注册」
（内嵌 turb 的 WebUI，授权头 `x-auth-code: omni2api-local`）。
