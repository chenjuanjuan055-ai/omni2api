# MiniMax 自动注册（omni2api 内置）

批量注册 MiniMax 账号并产出凭据，供 omni2api 的 MiniMax 反代使用。

## 组成

| 文件 | 作用 |
|---|---|
| `auto_mmx9.py` | 注册主脚本：camoufox 驱动浏览器走 `agent.minimax.io` → `account.minimax.io/unified-login` → 收验证码 → 拿 token |
| `bridge_server.py` | 邮箱桥接：建临时邮箱、收验证码、落盘凭据（HTTP 服务） |
| `data/` | 运行时数据（token、邮箱库、出口清单），**不进仓库** |

## 依赖

* **camoufox**（反检测浏览器）：装在独立的 Python 里，不是主服务环境。
  默认 `C:\Users\xi\AppData\Local\Programs\Python\Python312\python.exe`。
* **出口代理**：必须能通 `agent.minimax.io` 的境外 IP（直连会跳国内站 `agent.minimax.cn`）。
* **邮箱服务**：DuckMail，主密钥通过环境变量 `MMX_DUCKMASTER` 传，不进仓库。

## 出口池（共享端口 + rules 分流）

MiniMax 会对**同一个出口 IP 连续注册**降级成 "Welcome to MiniMax" 形态（点 Continue 不跳密码页），
所以每个账号必须换一个出口。

### 为什么要分流

**reCAPTCHA 的 enterprise.js 必须能加载**，否则 `window.grecaptcha` 一直是 undefined，
表单点了没反应，页面根本不会推进。机场/反代节点常常连不上 google 系域名，
所以把 google/recaptcha 单独路由到本机 v2rayN，minimax 域名才走池子里轮换的节点。

```yaml
proxies:
  - {name: "V2RAY-LOCAL", type: http, server: 127.0.0.1, port: 10808}
proxy-groups:
  - name: POOL
    type: select
    proxies: [节点1, 节点2, ...]
rules:
  - DOMAIN-SUFFIX,minimax.io,POOL        # minimax 走轮换节点
  - DOMAIN-SUFFIX,recaptcha.net,V2RAY-LOCAL   # google/recaptcha 走本地 v2rayN
  - MATCH,POOL
```

* **出口轮换**：每轮注册前 `PUT /proxies/POOL {"name": "<节点>"}` 切节点，
  浏览器连 `socks5://127.0.0.1:7896`（共享 mixed-port）。
* 注册是**串行**的，所以共享端口切节点不会互相打架。
* 源配置只要有 `proxies:` 段即可，服务层负责合成 proxy-groups 和 rules。
* `mode: rule` 必须的（源配置里可能是 `mode: global`，那样 rules 会失效）。
* `ipv6: false` 必须开：开着的话所有节点的落地会聚成同一个 IPv6。

出口清单落在 `data/`：

| 文件 | 内容 |
|---|---|
| `px_ports.json` | 池里所有节点名（候选） |
| `px_live.json` | 探活过的出口（节点名、落地 IP、实测耗时，按速度排序） |
| `px_state.json` | 每个节点的使用记录（本轮用过 / 已判定为死节点） |

### 探活

逐个切 POOL 节点再测（**必须串行**：共享端口上切节点是全局动作）。
命中率通常 15%~40%（大量节点连不上），失败会重试一次。
清单 6 小时内复用，不会每次重探。

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `MMX_BRIDGE_PORT` | `18791` | 邮箱桥接端口 |
| `MMX_DUCKMASTER` | 空 | 邮箱服务主密钥（必填） |
| `MMX_DUCK_API` | `https://api.duckmail.sbs` | 邮箱服务地址 |
| `MMX_PROXY` | `http://127.0.0.1:10808` | 单出口回落（没有可用池出口时） |
| `MMX_PASSWORD` | `Aa123456!` | 注册用的密码 |
| `MMX_TOKEN_FILE` | `data/minimaxtoken.txt` | 凭据落盘位置 |
| `MMX_WATCHDOG_SECS` | `0` = 自动 | 进程看门狗秒数；0 时按数量算 `180 + 200 × 个数` |
| `MMX_KILL_MIHOMO` | `0` | 置 `1` 才允许看门狗杀 mihomo（默认不杀，出口池是共享的） |

## 单独跑

```powershell
$env:MMX_BRIDGE_PORT = "18791"
$env:MMX_DUCKMASTER = "<主密钥>"
python bridge_server.py          # 必须先起，否则脚本第一句 health 失败就退出
python auto_mmx9.py 5           # 跑 5 个
```

## 从控制台跑

控制台 **MiniMax 注册** 页（`/minimax/register`）：

```
POST /api/minimax-reg/start          {"count": 5}
GET  /api/minimax-reg/status
GET  /api/minimax-reg/tokens?limit=50
GET  /api/minimax-reg/settings
POST /api/minimax-reg/settings       （改 python 路径 / 出口 / 主密钥 / pool_config 等）
```

配置落在 `config.json` 的 `minimax_reg` 段（`config.json` 不进仓库）。
主服务只做编排，浏览器与邮箱都在子进程里跑。

## 现网流程（2026-11 实测）

```
直达 https://account.minimax.io/unified-login     ★ 别从首页点 "Log in" 进来
  → 填邮箱 → 勾条款 → Continue
  → Create a password 页（填密码 + 再勾条款 + Continue）
  → 验证码页（input[placeholder*="verification"]）→ 收码 → **再勾一次条款** → Continue
  → 拿 token（localStorage._token），写进 data/minimaxtoken.txt
```

★ 从首页点 "Log in" 进来，URL 会带 `?login_redirect=...`（OAuth 变体），
那个形态下点 Continue **毫无反应** —— 页面看着一切正常，就是不推进。
直接进干净的 `unified-login` 才是注册表单。

## 踩过的坑

### 一、表单提交

1. **条款必须真的勾上**。对照实验（同一出口、同一流程，8 组）：
   勾了 → `oauth2/login` 200；没勾 → **连请求都不发**。
   服务端不报错、页面不跳转，静默失败。
2. **条款定位**：以 "I have read" 文本为锚点，往上找 3 层祖先，
   在每层里取第一个 10~24px 的 `<button>`，用**真实鼠标坐标**点击。
   ★ 别用泛化选择器：`button[data-state]` 会命中 "Sign up / Sign in" 切换 tab，
   点到就把页面切到登录表单，提交直接 403。
   ★ `button[class*="mt-[2px]"]` 也不够准（页面上有别的同 class 小按钮）。
3. **每个页面都要重新勾条款**：换页后条款状态是**重置**的。
   登录页、密码页、验证码页各勾一次。
4. **Continue 只认 `<button>`**：页面上还有一个 `<a>Continue</a>`（欢迎页的 CTA），
   点它不会触发表单提交 —— 实测点击后连 `oauth2/login` 都不发。
5. **Continue 用 JS 的 `element.click()`**：实测 JS click 能 work，
   Playwright 的 `locator.click()` 反而不行（命中测试会等超时）。
6. **邮箱不要重复 fill**：填一次 → 200；点之前再 fill 一次 → 403。
   填完等 1 秒再点条款。
7. **点完 Continue 要检查有没有前进，没前进就再点**（最多 3 轮），
   并能自己从欢迎页走回登录页（`goto_login()`：点 Sign in → 重填邮箱 → 勾条款）。
8. **输入框用 `fill` 而不是 `click`**：刚跳过来的页面可能有动画/弹窗挡住输入框，
   `Locator.click` 会一直等到 30 秒超时。

### 二、出口与代理

9. **出口 IP 会被 MiniMax 按 IP 限流**：同一出口注册几次后 `oauth2/login` 就 403
   或者干脆不发请求。⇒ 出口候选要**随机取**（别老是拿最快的几个），
   失败的出口标记 bad（6 小时 TTL），坏出口要持续换新的。
10. **reCAPTCHA 必须能加载**：脚本加载不了 google 系域名时 `grecaptcha` 一直 undefined，
    表单点了没反应 ⇒ 可以把 google/recaptcha 单独路由到本地 v2rayN（10808）。
    ⚠ 但 recaptcha token 是**绑定 IP** 的，分流出去就与页面 IP 不一致，
    token 可能发不出来 —— 所以**默认不分流**（`SPLIT_GOOGLE = False`）。
11. **出口探活必须串行**：共享端口上切节点是全局动作，并发会互相踩；
    节点命中率低（15%~40%），失败要重试一次。
12. **出口速度差 5 倍**：快的 8 秒开首页、慢的 38 秒 ⇒ 超时给到 150 秒。
13. **老的住宅代理池已经失效**：原版那批带认证的 `:3129` HTTP 代理现在全部
    `407 Proxy Authentication Required`，BrightData 那批 `138.199.35.x:9002` 也连不上。

### 三、工程

14. **看门狗要按数量放大**：注册是串行的，一个约 90~180 秒；固定 330 秒会把跑到一半的账号腰斩。
15. **看门狗不许杀 mihomo**：出口池是共享的（机器上还可能有别的 mihomo），
    `taskkill /IM mihomo.exe` 会无差别全杀。出口池由服务层管，跑批期间还有守护线程拉起它。
16. **子进程输出必须 UTF-8**：Windows 默认 GBK，日志里的 `✗` 会 `UnicodeEncodeError`
    把脚本整个带崩。
17. **登录按钮文案改成 "Log in"**：旧判定只认 `sign in` / `登录` / `登入` ⇒ 永远找不到登录键
    （现在直达登录页，这一步只在兜底回退时用）。
18. **mails_store 路径要两边一致**：桥接写 `data/`，脚本读 `BASE/`，不一致会 FileNotFoundError。
19. **直连会跳国内站**：`agent.minimax.cn`，必须走代理。
20. **调试看接口日志**：xhr/fetch 的请求和返回码都打出来，
    一眼能分清"没发请求"（前端问题）和"服务端拒绝"（风控问题）。

## 产出格式

`data/minimaxtoken.txt`，每行 `邮箱----时间----token`，token 是 JWT（有效期约 40 天）。
