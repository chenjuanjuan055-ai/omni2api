#!/usr/bin/env python
"""auto_mmx9.py - 最终版（split 路由：minimax 走代理池，google 走 10808）

★ 关键突破（vs v8）:
  reCAPTCHA 的 enterprise.js 必须能加载！
  代理池屏蔽 google.com → 用 mihomo split 把 google 路由到 10808

路由:
  minimax 域名  → 代理池出口（注册 IP）
  google/recaptcha → 10808
  CDN/埋点      → 直连

用法: python auto_mmx9.py [N]
"""
import asyncio
import json
import os
import random
import re
import subprocess
import sys
import time

import requests

# ★ 输出强制 UTF-8：Windows 默认 GBK，日志里的 ✗ / ★ 会直接
#   UnicodeEncodeError 把整个脚本干掉（子进程 stdout 是管道时尤其明显）。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE, "data")
try:
    os.makedirs(DATA_DIR, exist_ok=True)
except Exception:
    pass


def _env(name, default=""):
    return os.environ.get(name, default) or default


BRIDGE = "http://127.0.0.1:%s" % _env("MMX_BRIDGE_PORT", "18791")
DUCK = _env("MMX_DUCK_API", "https://api.duckmail.sbs")
OUT = _env("MMX_TOKEN_FILE", os.path.join(DATA_DIR, "minimaxtoken.txt"))
ONLY = _env("MMX_TOKEN_ONLY_FILE", os.path.join(DATA_DIR, "minimaxtoken_only.txt"))
PWD = _env("MMX_PASSWORD", "Aa123456!")
START_URL = _env("MMX_START_URL", "https://agent.minimax.io")

# ★ 直接进登录页用这个干净 URL：
#   从首页点 Sign in 进来会带 ?login_redirect=... 参数（OAuth 变体），
#   那个形态下表单点了没反应 —— 页面看着一切正常，但 oauth2/login 根本不发请求。
UNIFIED_LOGIN = _env("MMX_UNIFIED_LOGIN", "https://account.minimax.io/unified-login")
MIHOMO = _env("MMX_MIHOMO", r"D:/xi/Ai/smart-proxy/mihomo.exe")
CHEAP_PROXY = _env("MMX_PROXY", "http://127.0.0.1:10808")   # 能通 google 的出口

# ★ 出口池文件与 mails_store 一样放在 data/ 下（两处路径必须一致）
PX_LIVE = os.path.join(DATA_DIR, "px_live.json")
PX_PORTS = os.path.join(DATA_DIR, "px_ports.json")
PX_STATE = os.path.join(DATA_DIR, "px_state.json")

# ★ 出口池：共享端口 + rules 分流（minimax 走轮换节点，google/recaptcha 走本地 v2rayN）
POOL_MIXED = int(_env("MMX_POOL_PORT", "7896"))
POOL_CTL = int(_env("MMX_POOL_CTL", "9098"))
POOL_GROUP = _env("MMX_POOL_GROUP", "POOL")

POOL_DOMAINS = [
    "agent.minimax.io", "agent.minimaxi.com", "api.minimax.io", "api.minimaxi.com",
    "account.minimaxi.com", "account.minimax.io", "www.minimax.io", "minimax.io",
    "minimaxi.com", "hailuoai.com", "platform.minimax.io", "hailuo.ai",
    "cdn.hailuo.ai", "file.cdn.minimax.io", "filecdn.minimax.chat",
    "ip-api.com", "api.ipify.org", "ipinfo.io",
]
GOOGLE_DOMAINS = [
    "google.com", "gstatic.com", "recaptcha.net", "googleapis.com",
    "google-analytics.com", "googletagmanager.com", "doubleclick.net",
    "googleads.g.doubleclick.net",
]
DIRECT_DOMAINS = [
    "gtimg.com", "qcloud.com", "qq.com", "tencent.com", "tencentcs.com",
    "t.co", "twitter.com", "facebook.com", "connect.facebook.net",
    "bat.bing.com", "reddit.com", "redditstatic.com", "data.hailuo.ai",
]


def log(*a):
    print(*a, flush=True)


def load_candidates():
    """读出口候选。

    px_live.json  = 已探活过的出口（含真实落地 IP，按实测速度排序）
    px_ports.json = 出口池里所有 listener 端口（端口即出口，未探活）

    ★ 多 listener 池：每个端口用 ``proxy:`` 钉死一个节点，端口本身就代表一个出口，
      天然隔离，不需要在共享端口上切节点（切节点在并发下会互相打架）。

    两份都收：预探活只拿到一两个也不要紧，后面还有几百个端口可以现场验。
    """
    out = []
    seen_ports = set()   # 节点名
    try:
        live = json.load(open(PX_LIVE, encoding="utf-8"))
    except Exception:
        live = []
    for x in live or []:
        if not isinstance(x, dict):
            continue
        node = str(x.get("node") or x.get("name") or "")
        if node:
            seen_ports.add(node)
        out.append({
            "ip": x.get("ip") or "",
            "url": x.get("url") or ("http://127.0.0.1:%d" % POOL_MIXED),
            "node": node,
            "port": POOL_MIXED,
        })
    try:
        ports = json.load(open(PX_PORTS, encoding="utf-8"))
    except Exception:
        ports = []
    for x in ports or []:
        if not isinstance(x, dict):
            continue
        node = str(x.get("node") or x.get("name") or "")
        if not node or node in seen_ports:
            continue
        seen_ports.add(node)
        out.append({
            "ip": "",
            "url": "http://127.0.0.1:%d" % POOL_MIXED,
            "node": node,
            "port": POOL_MIXED,
        })
    return out


def verify_exit(px):
    """在线验证这个出口能不能打开 MiniMax，顺带取真实落地 IP。

    返回 (是否可用, 落地 IP)。代理池里死节点很多，不验一下会白跑一轮浏览器。
    """
    node = str(px.get("node") or "")
    # 本地额外出口（如 local-20200）用它自己的端口，不要当成池口
    port = px.get("port") or POOL_MIXED
    try:
        port = int(port)
    except Exception:
        port = POOL_MIXED
    try:
        import urllib.request as _u
        # ★ 先把出口切到这个节点（共享端口，选中谁决定出口）。
        #   注意：只有走本池 7896 的出口才需要切；
        #   本机其它代理工具的端口（20200/10808 等）是独立出口，切 POOL 会 400。
        _is_pool = (port == POOL_MIXED)
        if node and _is_pool:
            try:
                _req = _u.Request("http://127.0.0.1:%d/proxies/%s" % (POOL_CTL, POOL_GROUP),
                                  method="PUT",
                                  data=json.dumps({"name": node}).encode("utf-8"))
                _u.urlopen(_req, timeout=8).read()
            except Exception as e:
                log("  [exit] 切节点失败 %s（%s）" % (node, str(e)[:40]))
                return False, ""
        op = _u.build_opener(_u.ProxyHandler({
            "http": "http://127.0.0.1:%d" % port,
            "https": "http://127.0.0.1:%d" % port,
        }))
        # ★ 先验真正要紧的：能不能打开 MiniMax 首页。
        #   死节点在这一步就要暴露，别把 30 秒耗在无关的 IP 查询上。
        t0 = time.time()
        _r = op.open(_u.Request("https://agent.minimax.io/",
                                headers={"User-Agent": "Mozilla/5.0 Chrome/140.0.0.0"}),
                     timeout=15)
        if _r.status != 200:
            return False, ""
        ms = int((time.time() - t0) * 1000)
        ip = ""
        try:
            _r = op.open(_u.Request("http://ip-api.com/json/?fields=query",
                                    headers={"User-Agent": "Mozilla/5.0 Chrome/140.0.0.0"}),
                         timeout=10)
            _m = re.search(r'"query"\s*:\s*"([^"]+)"',
                           _r.read(300).decode("utf-8", "replace"))
            if _m:
                ip = _m.group(1)
        except Exception:
            pass
        log("  [exit] %s 可用 %s（%dms）" % (px.get("node") or px.get("port"), ip, ms))
        return True, ip
    except Exception as e:
        log("  [exit] %s 不可用（%s）" % (px.get("node") or px.get("port"), str(e)[:40]))
        return False, ""


def take_proxy():
    """挑一个没用过、且当场验证能通的出口。

    ★ 去重 key 用节点名，不是出口 ip：
      出口池里多个节点的落地 IP 往往相同（同一机场的不同入口），
      按 ip 去重会把它们全判成"已用过"而跳过，最后只剩单出口，
      撞上 MiniMax 对同 IP 的降级（Welcome 形态）。
    """
    cands = load_candidates()
    try:
        st = json.load(open(PX_STATE, encoding="utf-8"))
    except Exception:
        st = {}
    # ★ 候选顺序打散：老是拿最快的几个出口，很快就轮到被 MiniMax 按 IP 限流
    #   （同一出口注册几次后 oauth2/login 直接 403 / 不发请求）。
    try:
        random.shuffle(cands)
    except Exception:
        pass
    # ★ 本轮没用过的优先（随机之后仍尽量不重复用同一个出口）
    _fresh = [x for x in cands
              if not (st.get(str(x.get("node") or x.get("ip") or "")) or {}).get("used")]
    if _fresh:
        cands = _fresh + [x for x in cands if x not in _fresh]
    # ★ 本机额外出口（Clash Verge / v2rayN 的高速线路、家宽）质量明显好于机场节点，
    #   优先用它们 —— 机场节点那批 IP 段经常被目标站按段封。
    _local = [x for x in cands if str(x.get("node") or "").startswith("local-")]
    if _local:
        cands = _local + [x for x in cands if x not in _local]
    _now = time.time()
    for x in cands:
        key = str(x.get("node") or x.get("ip") or "")
        rec = st.get(key) or {}
        if rec.get("used"):
            continue
        if rec.get("bad"):
            # ★ bad 标记 6 小时后失效（风控窗口过了就重新用）
            if _now - float(rec.get("ts") or 0) < 6 * 3600:
                continue
        ok, ip = verify_exit(x)
        if not ok:
            # 死节点记下来，后面不再试
            rec["bad"] = 1
            rec["ts"] = time.time()
            st[key] = rec
            try:
                json.dump(st, open(PX_STATE, "w", encoding="utf-8"), indent=1)
            except Exception:
                pass
            continue
        if ip:
            x["ip"] = ip
        st[key] = {"used": 1, "ts": time.time()}
        try:
            json.dump(st, open(PX_STATE, "w", encoding="utf-8"), indent=1)
        except Exception:
            pass
        return x
    # ★ 没有可用出口时回落到配置的出口。
    #   start_tunnel 本来就直接用这个出口，候选池只是旧版本遗留的守门，
    #   没有它就直接 break 会导致一台都跑不了。
    if CHEAP_PROXY:
        return {"url": CHEAP_PROXY, "ip": CHEAP_PROXY, "country": "", "fallback": True}
    return None


def start_tunnel(port, exit_url):
    """起一个指向目标出口的本地代理口（供浏览器用 socks5）。

    2026-11-08：原实现固定用 10808，导致每一轮都从同一个出口 IP 出去，
    MiniMax 很快把该 IP 降级成 "Welcome to MiniMax" 形态（点 Continue 不跳）。
    现在按传入的 exit_url 起一个本地 HTTP->socks5 转发口（端口即 port），
    不同轮次换不同出口即可避开降级。
    """
    import urllib.request
    target = exit_url or CHEAP_PROXY
    # 目标本身就是一个本地代理口（如 mihomo 的 mixed-port）=> 直接复用，别再套一层
    if target.startswith("http://127.0.0.1:") or target.startswith("http://localhost:"):
        try:
            _p = int(target.rsplit(":", 1)[-1])
            log("  [tunnel] 复用本地代理口 %d" % _p)
            return _p
        except Exception:
            pass
    try:
        return _start_local_socks(port, target)
    except Exception as e:
        log("  [tunnel] 本地转发失败(%s)，回退直连出口 %s" % (str(e)[:60], CHEAP_PROXY))
        return CHEAP_PROXY


def _start_local_socks(port, target):
    """起一个本地 HTTP 代理口（转发到 target），返回端口号。

    用 python 的最小 TCP 转发实现，不引额外依赖；只做 HTTP CONNECT + 普通 HTTP。
    """
    import socket
    import threading

    def pipe(a, b):
        try:
            while True:
                data = a.recv(65536)
                if not data:
                    break
                b.sendall(data)
        except Exception:
            pass
        finally:
            for s_ in (a, b):
                try:
                    s_.close()
                except Exception:
                    pass

    def handle(c):
        try:
            c.settimeout(25)
            head = b""
            while b"\r\n\r\n" not in head and len(head) < 65536:
                chunk = c.recv(4096)
                if not chunk:
                    return
                head += chunk
            if not head:
                return
            line = head.split(b"\r\n", 1)[0].decode("latin-1")
            method = line.split(" ")[0].upper()
            hostport = target.split("://")[-1]
            if "://" in target:
                hostport = target.split("://", 1)[1]
            host, _, tp = hostport.partition(":")
            tp = int(tp or (443 if method == "CONNECT" else 80))
            up = socket.create_connection((host, tp), timeout=20)
            if method == "CONNECT":
                c.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                rest = head.split(b"\r\n\r\n", 1)[1] if b"\r\n\r\n" in head else b""
                if rest:
                    up.sendall(rest)
            else:
                up.sendall(head)
            t1 = threading.Thread(target=pipe, args=(c, up), daemon=True)
            t2 = threading.Thread(target=pipe, args=(up, c), daemon=True)
            t1.start(); t2.start()
        except Exception:
            try:
                c.close()
            except Exception:
                pass

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen(64)

    def loop():
        while True:
            try:
                conn, _ = srv.accept()
                threading.Thread(target=handle, args=(conn,), daemon=True).start()
            except Exception:
                break

    threading.Thread(target=loop, daemon=True).start()
    return port
    for _try in range(3):
        try:
            req = urllib.request.Request("https://agent.minimax.io/",
                                         headers={"User-Agent": "Mozilla/5.0 Chrome/140.0.0.0"})
            with op.open(req, timeout=25) as r:
                if r.status == 200:
                    try:
                        return int(target.rsplit(":", 1)[-1])
                    except Exception:
                        return 10808
        except Exception:
            pass
        time.sleep(1.5)
    return None


def _old_start_tunnel(port, exit_url):
    """启 mihomo split 路由（★ 先杀旧的，再验证就绪）"""
    # ★ 关键：杀掉旧 mihomo（避免端口冲突 + 僵死进程）
    try:
        subprocess.run(["taskkill", "/F", "/IM", "mihomo.exe"],
                       capture_output=True, timeout=10)
    except Exception:
        pass
    time.sleep(1.5)

    body = exit_url.split("//", 1)[1]
    creds, hp = body.rsplit("@", 1)
    user, pwd = creds.split(":", 1)
    host, hport = hp.rsplit(":", 1)

    rules = []
    for d in DIRECT_DOMAINS:
        rules.append("DOMAIN-SUFFIX,%s,DIRECT" % d)
    for d in GOOGLE_DOMAINS:
        rules.append("DOMAIN-SUFFIX,%s,GGL" % d)
    for d in POOL_DOMAINS:
        rules.append("DOMAIN-SUFFIX,%s,POOL" % d)
    rules.append("MATCH,DIRECT")

    cfg = {
        "mixed-port": port, "allow-lan": False, "log-level": "silent",
        "tcp-concurrent": True, "connect-timeout": 5000,
        "mode": "rule", "dns": {"enable": False},
        "proxies": [
            {"name": "POOL", "type": "http", "server": host, "port": int(hport),
             "username": user, "password": pwd, "tls": False},
            {"name": "GGL", "type": "http", "server": "127.0.0.1", "port": 10808,
             "tls": False},
        ],
        "rules": rules,
    }
    rundir = os.path.join(BASE, "split_pool", "run_%d" % port)
    os.makedirs(rundir, exist_ok=True)
    p = os.path.join(rundir, "config.json")
    json.dump(cfg, open(p, "w", encoding="utf-8"), ensure_ascii=False)
    subprocess.Popen([MIHOMO, "-f", p, "-d", rundir],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=0x00000008 | subprocess.CREATE_NO_WINDOW)

    # ★ 验证就绪（最多 15 秒）
    import urllib.request
    for _ in range(15):
        time.sleep(1)
        try:
            op = urllib.request.build_opener(urllib.request.ProxyHandler(
                {"http": "http://127.0.0.1:%d" % port,
                 "https": "http://127.0.0.1:%d" % port}))
            req = urllib.request.Request(
                "https://agent.minimax.io/",
                headers={"User-Agent": "Mozilla/5.0 Chrome/140.0.0.0"})
            with op.open(req, timeout=8) as r:
                if r.status == 200:
                    return port
        except Exception:
            pass
    return None   # ★ 没就绪 → 返回 None


def read_code(email, timeout=240):
    # ★ 与 bridge_server.py 的落盘位置保持一致（都在 data/ 下）
    p = os.path.join(DATA_DIR, "mails_store.jsonl")
    pwd = None
    for l in open(p, encoding="utf-8"):
        try:
            d = json.loads(l)
            if d.get("email") == email:
                pwd = d.get("pwd")
        except Exception:
            pass
    if not pwd:
        return None
    end = time.time() + timeout
    seen = set()
    while time.time() < end:
        try:
            tk = requests.post(DUCK + "/token",
                               json={"address": email, "password": pwd},
                               timeout=20).json()["token"]
            r = requests.get(DUCK + "/messages",
                             headers={"Authorization": "Bearer " + tk}, timeout=20)
            j = r.json()
            arr = j.get("hydra:member") if isinstance(j, dict) else j
            for m in (arr or []):
                mid = m.get("id")
                if mid in seen:
                    continue
                seen.add(mid)
                r2 = requests.get(DUCK + "/messages/%s" % mid,
                                  headers={"Authorization": "Bearer " + tk}, timeout=20)
                if r2.status_code != 200:
                    continue
                d = r2.json()
                txt = (d.get("subject", "") + " " + (d.get("text") or ""))
                mt = re.search(r"(\d{4,8})", txt)
                if mt and re.search(r"verif|code|验证", txt, re.I):
                    return mt.group(1)
        except Exception:
            pass
        time.sleep(4)
    return None


async def safe_mouse(page, act, x, y, steps=5, tmo=8):
    """★ mouse 操作加超时保护（防止页面卡死时 hang 住）"""
    try:
        if act == "move":
            await asyncio.wait_for(page.mouse.move(x, y, steps=steps), timeout=tmo)
        else:
            await asyncio.wait_for(page.mouse.click(x, y), timeout=tmo)
        return True
    except Exception:
        return False


async def rc_token_len(page) -> int:
    """reCAPTCHA token 的实际长度（0 = 还没出 token）。"""
    try:
        return int(await page.evaluate(
            """() => {
                const ta = document.querySelector('textarea[name="g-recaptcha-response"]');
                if (!ta) return 0;
                return (ta.value || '').length;
            }""") or 0)
    except Exception:
        return 0


async def wait_rc_token(page, max_secs: int = 12) -> bool:
    """等 reCAPTCHA token 真正有值。

    ★ 只等 textarea 存在是不够的（那种情况 value 可能还是空串）。
      token 没值时点 Continue 表单不提交，页面会被踢回 "Welcome to MiniMax"，
      后面既找不到密码框，也收不到验证码。
    """
    t0 = time.time()
    while time.time() - t0 < max_secs:
        if await rc_token_len(page) > 20:
            return True
        await page.wait_for_timeout(1500)
    return False


async def page_form(page) -> dict:
    """当前处在哪个形态（登录页 / 密码页 / 验证码页 / 欢迎页）。"""
    try:
        return await page.evaluate(r"""() => ({
            pwd: !!document.querySelector('input[type=password]'),
            code: !!document.querySelector('input[placeholder*="verification" i], input[inputmode="numeric"]'),
            email: !!document.querySelector('input[type=email]'),
            welcome: /welcome to minimax|sign in or create an account/i.test(document.body.innerText || '')
        })""")
    except Exception:
        return {}


async def check_terms(page) -> bool:
    """勾选协议。

    ★ 用 Playwright 的**元素点击**（自动 scrollIntoView + 命中测试），
      坐标点击（page.mouse.click(x, y)）会概率性点空 ——
      表现为"条款没勾上 → 连 oauth2/login 都不发"，页面毫无反应。
    ★ 定位：以 "I have read" 文本为锚点，往上找 3 层祖先，
      在每层里取第一个 10~24px 的 <button>（和实测能work的写法一致）。
      别用泛化选择器：button[data-state] 会命中 "Sign up / Sign in" 切换 tab，
      点到就把页面切到登录表单，提交直接 403。
    ★ 不做幂等判断：每个页面只勾一次（换页后条款本来就是重置的）。
    """
    idx = await page.evaluate("""() => {
        const btns = [...document.querySelectorAll('button')];
        for (const e of document.querySelectorAll('*')) {
            const t = (e.textContent || '').trim();
            if (t.startsWith('I have read') && t.length < 150) {
                let row = e;
                for (let i = 0; i < 3 && row; i++) {
                    for (const b of row.querySelectorAll('button')) {
                        const r = b.getBoundingClientRect();
                        if (r.width >= 10 && r.width <= 24)
                            return btns.indexOf(b);
                    }
                    row = row.parentElement;
                }
            }
        }
        return -1;
    }""")
    if idx is None or idx < 0:
        return False
    ok = False
    try:
        btns = page.locator('button')
        if idx < await btns.count():
            await btns.nth(idx).click(timeout=6000, force=True)
            ok = True
    except Exception:
        ok = False
    if not ok:
        # 兜底：坐标点击
        try:
            box = await page.evaluate("""() => {
                const btns = [...document.querySelectorAll('button')];
                for (const e of document.querySelectorAll('*')) {
                    const t = (e.textContent || '').trim();
                    if (t.startsWith('I have read') && t.length < 150) {
                        let row = e;
                        for (let i = 0; i < 3 && row; i++) {
                            for (const b of row.querySelectorAll('button')) {
                                const r = b.getBoundingClientRect();
                                if (r.width >= 10 && r.width <= 24)
                                    return {x: r.x + r.width/2, y: r.y + r.height/2};
                            }
                            row = row.parentElement;
                        }
                    }
                }
                return null;
            }""")
            if box:
                await safe_mouse(page, "move", box["x"], box["y"], steps=4)
                await page.wait_for_timeout(250)
                await safe_mouse(page, "click", box["x"], box["y"])
        except Exception:
            pass
    await page.wait_for_timeout(900)
    return True


async def terms_state(page) -> dict:
    """条款勾选状态（shadcn 的 checkbox 是 role=checkbox + data-state）。"""
    try:
        return await page.evaluate(r"""() => {
            const cbs = [...document.querySelectorAll('button[role=checkbox], [role=checkbox], button[data-state], input[type=checkbox]')];
            const info = cbs.slice(0, 6).map(c => ({
                tag: c.tagName, role: c.getAttribute('role') || '',
                state: c.getAttribute('data-state') || '',
                aria: c.getAttribute('aria-checked') || '',
                checked: (c.checked === true) ? 1 : 0,
                cls: (c.className||'').toString().slice(0, 40)
            }));
            let label = null;
            for (const e of document.querySelectorAll('*')) {
                const t = (e.textContent || '').trim();
                if (t.startsWith('I have read') && t.length < 150) { label = t.slice(0, 60); break; }
            }
            return {cbs: info, label: label};
        }""")
    except Exception as e:
        return {"err": repr(e)[:60]}


async def click_terms(page) -> bool:
    """勾条款（老名字，转发到 check_terms）。

    ★ 旧实现用 `button[data-state]` 之类的泛化选择器，会误点
      "Sign up / Sign in" 切换 tab —— 页面被切到登录表单，
      提交 oauth2/login 直接 403。现在统一走 check_terms。
    """
    return await check_terms(page)


async def diag_state(page) -> dict:
    """把当前页面的关键状态抓出来（失败时定位用）。"""
    try:
        return await page.evaluate(r"""() => {
            const em = document.querySelector('input[type=email]');
            const pwd = document.querySelector('input[type=password]');
            const btns = [];
            for (const b of document.querySelectorAll('button')) {
                const t = (b.innerText||'').trim();
                if (!t || t.length > 24) continue;
                btns.push(t + (b.disabled ? '[D]' : ''));
            }
            const cbs = [...document.querySelectorAll('button[role=checkbox],[role=checkbox],button[data-state],input[type=checkbox]')];
            const cbInfo = cbs.slice(0,5).map(c => (c.getAttribute('data-state')||c.getAttribute('aria-checked')||'?') + '@' + (c.className||'').toString().slice(0,18));
            const forms = document.querySelectorAll('form').length;
            const txt = (document.body.innerText||'').replace(/\s+/g,' ').slice(0, 220);
            return {
                url: location.href.slice(0, 90),
                emailVal: em ? (em.value||'').slice(0,40) : null,
                hasPwd: !!pwd,
                forms: forms,
                cbs: cbInfo,
                btns: btns.slice(0, 14),
                txt: txt
            };
        }""")
    except Exception as e:
        return {"err": repr(e)[:80]}


async def refill_form(page, email, tag, terms: bool = False) -> bool:
    """重新填邮箱（可选地再勾条款）。

    ★ 关键坑 1：input 的 DOM value 有值 ≠ React state 有值。
      表单被重置过一次（条款点击触发重渲染 / 页面来回切）之后，
      DOM 里还留着邮箱，但 React state 是空的 —— 这时点 Continue
      根本不会发 oauth2/login 请求，页面看着一切正常就是不推进。
      所以每次点之前都重填一遍。
    ★ 关键坑 2：**不要重复点条款**。条款是开关，勾上后再点一次就是取消，
      服务端直接 403。所以默认 terms=False（⑥ 已经勾过了）。
    """
    ok = False
    try:
        loc = page.locator('input[type="email"]').first
        if await loc.count() > 0 and await loc.is_visible():
            await loc.fill(email, timeout=12000)
            await page.wait_for_timeout(600)
            ok = True
    except Exception:
        pass
    if terms:
        await check_terms(page)
    return ok


async def goto_login(page, email, tag) -> bool:
    """从欢迎页回到 unified-login：点 Sign in/Log in → 重填邮箱 → 勾条款。"""
    try:
        await page.evaluate(r"""() => {
            for (const e of document.querySelectorAll('button, a, [role=button]')) {
                const t = (e.innerText || '').trim();
                if (/(sign\s?in|log\s?in|create\s+account|sign\s?up)/i.test(t)) { e.click(); return true; }
            }
            return false;
        }""")
    except Exception:
        pass
    await page.wait_for_timeout(6000)
    if await refill_form(page, email, tag, terms=True):
        log("%s ↩ 回到登录页并重填邮箱" % tag)
        return True
    return False


async def click_btn(page, texts, near: str = "") -> dict:
    """点按钮。**用 JS 的 element.click()**（实测能work的那种）。

    ★ 为什么不用 Playwright 的 locator.click()：
      实测同一出口、同一流程，
        - JS  element.click()  → oauth2/login 200 ✅
        - Playwright locator.click() → oauth2/login 403 ❌
      所以这里就规规矩矩用 JS click，取**第一个**精确匹配的 <button>。
    ★ 只认 <button>：页面上还有一个 <a>Continue</a>（欢迎页的 CTA），
      点它不会触发表单提交 —— 实测点击后连 oauth2/login 都不发。
    ★ near 只是"同容器优先"的微调，取不到就退回第一个。
    """
    js = """(a) => {
        const all = [...document.querySelectorAll('button')];
        const exact = all.filter(b => {
            const t = (b.innerText||b.textContent||'').trim();
            return a.texts.indexOf(t) >= 0 && !b.disabled;
        });
        if (!exact.length) return {n: 0, hit: null};
        const near = (b) => {
            if (!a.near) return false;
            let x = b;
            for (let i = 0; i < 6 && x; i++) {
                if (x.querySelector && x.querySelector(a.near)) return true;
                x = x.parentElement;
            }
            return false;
        };
        let pick = exact.find(b => near(b));
        if (!pick) pick = exact[0];
        pick.click();
        return {n: exact.length, hit: (pick.innerText||'').trim()};
    }"""
    try:
        return await page.evaluate(js, {"texts": list(texts), "near": near})
    except Exception as e:
        return {"n": 0, "hit": None, "err": repr(e)[:60]}


async def main():
    from camoufox.async_api import AsyncCamoufox

    total = int(sys.argv[1]) if len(sys.argv) > 1 else 1

    # ★ 进程级看门狗：整体还没结束就自杀（防 Playwright 僵死）
    #   ★ 时长必须按数量放大：注册是串行的，一个约 90~180 秒，
    #     跑 5 个就是 10 分钟以上；固定 330 秒会把跑到一半的账号腰斩掉。
    import threading
    def _watchdog():
        _wd_secs = int(os.environ.get("MMX_WATCHDOG_SECS", "0") or 0)
        if _wd_secs <= 0:
            _wd_secs = 180 + 200 * total
        time.sleep(_wd_secs)
        log("!! 看门狗触发（%d秒），清理并退出" % _wd_secs)
        # ★ 关键：杀掉 camoufox 子进程（os._exit 不会杀子进程）
        try:
            subprocess.run(["taskkill", "/F", "/IM", "camoufox.exe"],
                           capture_output=True, timeout=15)
        except Exception:
            pass
        # ★ 绝不在这里杀 mihomo：
        #   出口池是共享的（而且机器上可能还有别的 mihomo 在跑），
        #   taskkill /IM mihomo.exe 会无差别全杀。出口池由服务层管。
        os._exit(2)
    _wd = threading.Thread(target=_watchdog, daemon=True)
    _wd.start()
    try:
        h = requests.get(BRIDGE + "/health", timeout=8).json()
        log("桥接 OK %s" % h)
    except Exception as e:
        log("桥接挂了: %s" % e)
        return

    log("=" * 62)
    log("  v9（split 路由）· 共 %d 个" % total)
    log("=" * 62)

    base_port = 24100
    ok = 0
    t0 = time.time()

    # ★ 失败不消耗账号名额：换出口重试同一个账号（最多 MAX_TRIES 次）。
    #   出口会被 MiniMax 按 IP 限流（同一出口注册几次后 oauth2/login 就 403），
    #   换个出口往往就能成。
    MAX_TRIES = 8
    i = 0
    tries = 0
    while i < total:
        tag = "[w%02d]" % i
        tries += 1
        if tries > MAX_TRIES:
            log("%s 重试 %d 次仍失败，换下一个账号" % (tag, MAX_TRIES))
            i += 1
            tries = 0
            await asyncio.sleep(8)
            continue
        try:
            m = requests.post(BRIDGE + "/mail", json={}, timeout=60).json()
            email = m.get("email")
            if not email:
                log("%s 建邮箱失败" % tag)
                continue
            log("%s 邮箱 %s" % (tag, email))

            px = take_proxy()
            if not px:
                log("%s 代理池空" % tag)
                break
            # ★ 轮换出口：同 IP 连续注册会被 MiniMax 降级成 Welcome 形态
            if px.get("ctl") and px.get("node"):
                try:
                    import urllib.request as _u
                    _req = _u.Request(str(px["ctl"]) + "/proxies/POOL", method="PUT",
                                       data=json.dumps({"name": px["node"]}).encode())
                    _u.urlopen(_req, timeout=6).read()
                    log("%s 🔀 出口 → %s" % (tag, px["node"]))
                except Exception as e:
                    log("%s 换节点失败(%s)" % (tag, str(e)[:50]))
            port = base_port + (i % 20)
            tport = start_tunnel(port, px["url"])
            if not tport:
                log("%s ✗ mihomo 未就绪，跳过（代理 %s）" % (tag, px["ip"]))
                continue
            log("%s 出口 %s (%s) → :%d" % (tag, px["ip"], px.get("country", ""), tport))

            async with AsyncCamoufox(
                    headless=False, geoip=False,
                    proxy={"server": "socks5://127.0.0.1:%d" % tport},
                    # ★ 关键：允许第三方脚本 + cookie + 关跟踪保护（reCAPTCHA 需要）
                    firefox_user_prefs={
                        "privacy.trackingprotection.enabled": False,
                        "privacy.trackingprotection.pbmode.enabled": False,
                        "network.cookie.cookieBehavior": 0,
                        "network.cookie.sameSite.noneRequiresSecure": False,
                        "dom.storage_access.enabled": False,
                        "privacy.partition.network_state": False,
                        "network.http.referer.XOriginPolicy": 0,
                        "security.mixed_content.block_active_content": False,
                        "browser.contentblocking.category": "standard",
                        "dom.security.https_only_mode": False,
                        "permissions.default.image": 1,
                    }) as br:
                page = await br.new_page()

                # ★ 调试：把 MiniMax 的接口请求/失败打出来
                #   （表单点了没反应时，靠这个区分"没发请求"和"服务端拒绝"）
                # ★ oauth2/login 返回 403 = 这个出口被目标站按 IP 限流了，
                #   别在同一 IP 上死磕，立刻换出口重试。
                _blocked = {"hit": False}

                def _on_resp(r):
                    try:
                        rt = r.request.resource_type
                    except Exception:
                        rt = ""
                    if rt in ("xhr", "fetch"):
                        log("  [api] %s %s" % (r.status, r.url[:78]))
                        if r.status == 403 and "/oauth2/login" in r.url:
                            _blocked["hit"] = True
                page.on("response", _on_resp)

                def _on_fail(r):
                    try:
                        rt = r.request.resource_type
                    except Exception:
                        rt = ""
                    if rt in ("xhr", "fetch"):
                        log("  [api] FAIL %s %s" % (str(r.failure)[:30], r.url[:70]))
                page.on("requestfailed", _on_fail)

                # ★ 关键补丁（batch8 有，v9 漏了）: 抹掉自动化痕迹
                await page.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                    try { delete Object.getPrototypeOf(navigator).webdriver; } catch(e) {}
                    if (!navigator.deviceMemory) {
                        Object.defineProperty(navigator, 'deviceMemory', {get: () => 8});
                    }
                    if (!window.chrome) { window.chrome = {runtime: {}}; }
                """)

                # ① 直达登录页
                # ★ 不要从首页点 Sign in 进来：那条路 URL 会带 ?login_redirect=...
                #   （OAuth 变体），那个形态下点 Continue 毫无反应。
                #   直接进干净的 unified-login，实测点 Continue 立刻进密码页。
                log("%s ① 直达登录页" % tag)
                try:
                    await page.goto(UNIFIED_LOGIN, wait_until="domcontentloaded",
                                    timeout=150000)
                except Exception as _e1:
                    log("%s ① ✗ 登录页超时/失败（%s）" % (tag, repr(_e1)[:50]))
                    continue
                ok_page = False
                for _ in range(12):
                    await page.wait_for_timeout(2500)
                    try:
                        has = await page.evaluate("""() => {
                            if (document.querySelector('input[type="email"]')) return true;
                            for (const e of document.querySelectorAll('button, a, [role="button"]')) {
                                const t = ((e.innerText||e.textContent||e.getAttribute('aria-label')||'')).trim().toLowerCase();
                                if (t.includes('log in') || t.includes('sign in') || t.includes('登录') || t.includes('登入')) return true;
                            }
                            return false;
                        }""")
                    except Exception:
                        has = False
                    if has:
                        ok_page = True
                        break
                if not ok_page:
                    # ★ 2026-10-10: reload 重试（总限 60 秒，防止卡死）
                    _t0 = time.time()
                    for _r in range(3):
                        if time.time() - _t0 > 60:
                            break
                        try:
                            await page.reload(wait_until="domcontentloaded", timeout=90000)
                        except Exception:
                            pass
                        if time.time() - _t0 > 60:
                            break
                        await page.wait_for_timeout(6000)
                        try:
                            has = await page.evaluate("""() => {
                            if (document.querySelector('input[type="email"]')) return true;
                                for (const e of document.querySelectorAll('button, a, [role="button"]')) {
                                    const t = ((e.innerText||e.textContent||e.getAttribute('aria-label')||'')).trim().toLowerCase();
                                    if (t.includes('log in') || t.includes('sign in') || t.includes('登录') || t.includes('登入')) return true;
                                }
                                return false;
                            }""")
                        except Exception:
                            has = False
                        if has:
                            ok_page = True
                            break
                if not ok_page:
                    log("%s ① ✗ 页面无登录键（reload 3 次仍失败，换代理）" % tag)
                    # ★ 标记该代理为坏（下次不再用）
                    try:
                        _p = os.path.join(BASE, "px_state.json")
                        _st = json.load(open(_p, encoding="utf-8")) if os.path.exists(_p) else {}
                        _st[px["ip"]] = {"used": 1, "bad": 1, "ts": time.time()}
                        json.dump(_st, open(_p, "w", encoding="utf-8"), indent=1)
                    except Exception:
                        pass
                    continue
                await page.wait_for_timeout(2000)

                # ★ 验证 reCAPTCHA 加载
                rc = await page.evaluate("""() => ({
                    obj: typeof window.grecaptcha,
                    scripts: [...document.querySelectorAll('script[src*="recaptcha"]')].length
                })""")
                log("%s   reCAPTCHA: %s" % (tag, json.dumps(rc)))

                # ② 关弹窗
                try:
                    el = page.locator('button[aria-label*="close" i]').first
                    if await el.count() > 0:
                        await el.click(timeout=6000)
                        await page.wait_for_timeout(2000)
                except Exception:
                    pass
                except BaseException:
                    pass

                # ③ Sign in —— 已经在登录页（直达进来的）就跳过
                _already = False
                try:
                    _e0 = page.locator('input[type="email"]').first
                    _already = (await _e0.count() > 0 and await _e0.is_visible())
                except Exception:
                    pass
                if _already:
                    log("%s ③ 已在登录页（直达），跳过 Sign in" % tag)
                    clicked = "直达"
                else:
                    b = await page.evaluate("""() => {
                        // ★ 取「最靠右上」的 Sign in（导航栏那个，跳过侧边栏）
                        const cands = [];
                        for (const e of document.querySelectorAll('button')) {
                            const t = (e.innerText||'').trim().toLowerCase();
                            if (t.includes('log in') || t.includes('sign in') || t.includes('登录') || t.includes('登入')) {
                                const r = e.getBoundingClientRect();
                                if (r.width > 0 && r.height > 0)
                                    cands.push({x: r.x + r.width/2, y: r.y + r.height/2,
                                                rx: r.x, ry: r.y});
                            }
                        }
                        if (!cands.length) return null;
                        // 按 (x 降序) 取最靠右的；优先 y < 100（导航栏）
                        const nav = cands.filter(c => c.ry < 120);
                        const pick = nav.length ? nav[nav.length-1]
                                                : cands.sort((a,b) => b.rx - a.rx)[0];
                        return {x: pick.x, y: pick.y};
                    }""")
                    if not b:
                        log("%s ③ ✗ 无 Sign in" % tag)
                        continue
                    # ★ 关键：导航栏用 JS 直接 click（鼠标点击无效）
                    clicked = await page.evaluate("""() => {
                        const cands = [];
                        for (const e of document.querySelectorAll('button, a, [role="button"]')) {
                            const t = (e.innerText||'').trim().toLowerCase();
                            if (t.includes('log in') || t.includes('sign in') || t.includes('登录') || t.includes('登入')) {
                                const r = e.getBoundingClientRect();
                                if (r.width > 0 && r.height > 0)
                                    cands.push({el: e, rx: r.x, ry: r.y});
                            }
                        }
                        if (!cands.length) return false;
                        // 优先导航栏（y<120）
                        const nav = cands.filter(c => c.ry < 120);
                        const pick = nav.length ? nav[0] : cands.sort((a,b) => b.rx - a.rx)[0];
                        pick.el.scrollIntoView({block:'center'});
                        pick.el.click();
                        return true;
                    }""")
                    log("%s ③ 点击 Sign in: %s" % (tag, clicked))
                    await page.wait_for_timeout(1200)

                # ④ 等邮箱框
                el = None
                for j in range(20):
                    await page.wait_for_timeout(2000)
                    try:
                        loc = page.locator('input[type="email"]').first
                        if await loc.count() > 0 and await loc.is_visible():
                            el = loc
                            break
                    except Exception:
                        # 页面跳转中 → 等下一轮
                        continue
                if not el:
                    # ★ 诊断：dump 当前按钮状态
                    try:
                        dbg = await page.evaluate("""() => {
                            const out = [];
                            for (const e of document.querySelectorAll('button')) {
                                const t = (e.innerText||'').trim();
                                if (!t || t.length > 20) continue;
                                const r = e.getBoundingClientRect();
                                if (r.width === 0) continue;
                                out.push(t + '@(' + Math.round(r.x) + ',' + Math.round(r.y) + ')');
                            }
                            return {url: location.href, win: [innerWidth, innerHeight], btns: out.slice(0,20)};
                        }""")
                        log("%s ④ ✗ 没登录页 | URL=%s" % (tag, dbg.get("url","?")[:55]))
                    except Exception:
                        log("%s ④ ✗ 没登录页" % tag)
                    # ★ reload 重试一次
                    try:
                        await page.reload(wait_until="domcontentloaded", timeout=90000)
                        await page.wait_for_timeout(6000)
                        await page.evaluate(r"""() => {
                            const c = [...document.querySelectorAll('button')].filter(b =>
                                /(log\s?in|sign\s?in|登录|登入)/i.test((b.innerText||b.textContent||'').trim()));
                            if (c.length) c[c.length-1].click();
                        }""")
                        await page.wait_for_timeout(7000)
                        for _j in range(8):
                            try:
                                l2 = page.locator('input[type="email"]').first
                                if await l2.count() > 0 and await l2.is_visible():
                                    el = l2
                                    log("%s ④ 重试成功 → 登录页" % tag)
                                    break
                            except Exception:
                                pass
                            await page.wait_for_timeout(2000)
                    except Exception:
                        pass
                    if not el:
                        continue
                log("%s ④ 登录页" % tag)
                try:
                    log("%s ④ URL=%s" % (tag, (await page.url)[:70]))
                except Exception:
                    pass

                # ⑤ 邮箱（★ click 可能被遮挡 → 用 fill + JS click 兜底）
                try:
                    await el.fill(email, timeout=15000)
                except Exception:
                    try:
                        await el.click(timeout=8000)
                        await el.fill(email, timeout=15000)
                    except Exception:
                        # JS 直接设值 + 触发 React 事件
                        await page.evaluate("""(em) => {
                            const i = document.querySelector('input[type="email"]');
                            if (!i) return;
                            const setter = Object.getOwnPropertyDescriptor(
                                window.HTMLInputElement.prototype, 'value').set;
                            setter.call(i, em);
                            i.dispatchEvent(new Event('input', {bubbles: true}));
                            i.dispatchEvent(new Event('change', {bubbles: true}));
                        }""", email)
                await page.wait_for_timeout(900)

                # ⑥ 协议（★ 填完邮箱缓 1 秒再勾，和实测能work的流程一致）
                await page.wait_for_timeout(1000)
                _t_ok = await click_terms(page)
                if not _t_ok:
                    box = await page.evaluate("""() => {
                        for (const e of document.querySelectorAll('*')) {
                            const t = (e.textContent || '').trim();
                            if (t.startsWith('I have read') && t.length < 150) {
                                let row = e;
                                for (let i = 0; i < 3 && row; i++) {
                                    for (const b of row.querySelectorAll('button')) {
                                        const r = b.getBoundingClientRect();
                                        if (r.width >= 10 && r.width <= 24)
                                            return {x: r.x + r.width/2, y: r.y + r.height/2};
                                    }
                                    row = row.parentElement;
                                }
                            }
                        }
                        return null;
                    }""")
                    if box:
                        await safe_mouse(page, "move", box["x"], box["y"], steps=4)
                        await page.wait_for_timeout(300)
                        await safe_mouse(page, "click", box["x"], box["y"])
                        await page.wait_for_timeout(1500)
                log("%s ⑥ 协议已勾(%s)" % (tag, "locator" if _t_ok else "坐标"))

                # ⑦ Continue（★ 精确匹配，避免点到 "Continue with Google/Github"）
                r7 = await page.evaluate("""() => {
                    for (const b of document.querySelectorAll('button')) {
                        const t = (b.innerText||'').trim();
                        // 精确等于 Continue / 继续
                        if ((t === 'Continue' || t === '继续') && !b.disabled) {
                            const r = b.getBoundingClientRect();
                            if (r.width > 5) return {x: r.x + r.width/2, y: r.y + r.height/2};
                        }
                    }
                    return null;
                }""")
                if r7:
                    # ★ 2026-11-08：直接 DOM click，不要先等 reCAPTCHA token。
                    #   旧逻辑先等 token（最多 25 秒），那 25 秒里页面被重定向成
                    #   "Welcome to MiniMax" 形态，之后点 Continue 就不生效。
                    #   实测：不等 token 直接 click 一次直达 Create a password 页。
                    _dom_ok = False
                    for _try in range(3):
                        # ★ 点之前稍等 reCAPTCHA（实测 token 的 value 一直是空的，
                        #   所以只做短暂等待，真正的杀手锏是下面这个「点完检查、
                        #   没前进就再点」的重试循环）。
                        # ★ 这里**不要**重复 fill 邮箱：
                        #   重复 fill 会让服务端对这次提交直接 403
                        #   （实测：填一次 → oauth2/login 200；填两次 → 403）。
                        #   邮箱由 ⑤ 填一次，条款由 ⑥ 勾一次，各管各的。
                        _tok = await wait_rc_token(page, 4)
                        _ck = await click_btn(page, ["Continue", "继续"],
                                              near="input[type=email]")
                        _dom_ok = bool(_ck.get("n"))
                        if _dom_ok:
                            log("%s ⑦ Continue (dom click, 第 %d 次, 同容器=%s, 共%d个)" % (
                                tag, _try + 1, _ck.get("hit"), _ck.get("n")))
                        await page.wait_for_timeout(7000)
                        if _blocked["hit"]:
                            # ★ 这个出口被限流了 → 跳出，换个出口重试
                            log("%s ⑦ 出口被限流(403)，换出口重试" % tag)
                            _bl = px.get("node") or px.get("ip")
                            try:
                                st2 = json.load(open(PX_STATE, encoding="utf-8"))
                                st2.setdefault(str(_bl), {})["bad"] = 1
                                st2[str(_bl)]["ts"] = time.time()
                                json.dump(st2, open(PX_STATE, "w", encoding="utf-8"), indent=1)
                            except Exception:
                                pass
                            break
                        _st = await page_form(page)
                        if _st.get("pwd") or _st.get("code"):
                            break
                        if not _dom_ok:
                            await page.wait_for_timeout(3000)
                        else:
                            # ★ 没推进就抓现场，后面靠这个定位
                            _d = await diag_state(page)
                            log("%s ⑦ 没推进 | email=%r forms=%s cbs=%s btns=%s | %s" % (
                                tag, _d.get("emailVal"), _d.get("forms"),
                                _d.get("cbs"), _d.get("btns"),
                                (_d.get("txt") or "")[:80]))
                    if not _dom_ok:
                        await safe_mouse(page, "move", r7["x"], r7["y"], steps=5)
                        await page.wait_for_timeout(300)
                        await safe_mouse(page, "click", r7["x"], r7["y"])
                        log("%s ⑦ Continue @(%.0f,%.0f) (mouse)" % (tag, r7["x"], r7["y"]))
                else:
                    log("%s ⑦ ✗ 没找到 Continue" % (tag))
                await page.wait_for_timeout(9000)

                # ⑧ 密码页
                try:
                    pel = page.locator("input[type=password]").first
                    # ★ 兜底：停在 "Welcome to MiniMax" 欢迎页时，
                    #   先点 "Sign in" / "Create account" / "Continue" 进 unified-login。
                    if not (await pel.count() > 0 and await pel.is_visible()):
                        # ★ 兜底：停在 "Welcome to MiniMax" 欢迎页时，重新进登录流程。
                        #   ★ 这里的 Continue 同样要 reCAPTCHA token 才生效，
                        #     所以先等 token 再点；还不行就回登录入口重填邮箱再来一轮。
                        for _wr in range(3):
                            _st = await page_form(page)
                            if _st.get("pwd") or _st.get("code"):
                                break
                            _tok = await wait_rc_token(page, 4)
                            # ★ 优先点跟邮箱框同容器的 Continue（欢迎页那个点了没用）
                            _ck8 = await click_btn(page, ["Continue", "继续"],
                                                  near="input[type=email]")
                            if _ck8.get("n"):
                                log("%s ⑧ 欢迎页 -> 点同容器 Continue (%s)" % (
                                    tag, _ck8.get("hit")))
                            else:
                                _w = await page.evaluate(r"""() => {
                                const txt = (document.body.innerText||'');
                                if (!/welcome to minimax|sign in or create an account/i.test(txt))
                                    return null;
                                // ★ 关键：这个形态下 Continue 必须先勾了条款才可点。
                                //   脚本在登录页勾的条款到这里已重置，所以要先补勾一次。
                                let terms = null;
                                for (const b of document.querySelectorAll('button')) {
                                    const cls = (b.className||'').toString();
                                    if (cls.includes('mt-[2px]')) { b.setAttribute('data-mmx-terms','1'); terms = 'marked'; break; }
                                }
                                const all = [...document.querySelectorAll('button,a,[role=button]')];
                                const want = [
                                    /(sign\s?in|log\s?in|create\s+account|sign\s?up|登录|注册|创建账号)/i,
                                    /^continue$/i, /^继续$/i,
                                ];
                                for (const rx of want) {
                                    for (const b of all) {
                                        const tt = (b.innerText||b.textContent||'').trim();
                                        if (tt && rx.test(tt) && !b.disabled) {
                                            b.click();
                                            return (terms ? 'terms+' : '') + tt.slice(0,30);
                                        }
                                    }
                                }
                                return terms;
                            }""")
                                if _w:
                                    log("%s ⑧ 欢迎页 -> %r 进登录流程 (token=%s)" % (
                                        tag, _w, "有" if _tok else "无"))
                                # 条款用 Playwright 真实点击补勾一次
                                try:
                                    _tt = page.locator('[data-mmx-terms="1"]')
                                    if await _tt.count() > 0:
                                        await _tt.first.click(timeout=6000)
                                except Exception:
                                    pass
                                await click_terms(page)
                            await page.wait_for_timeout(8000)
                            _st = await page_form(page)
                            if _st.get("pwd") or _st.get("code"):
                                break
                            # ★ 还停在欢迎页 → 回登录入口重填邮箱，再点一次 Continue
                            if _st.get("welcome"):
                                if not await goto_login(page, email, tag):
                                    break
                                await wait_rc_token(page, 4)
                                await click_btn(page, ["Continue", "继续"],
                                               near="input[type=email]")
                                await page.wait_for_timeout(9000)
                    if await pel.count() > 0 and await pel.is_visible():
                        # ★ 别急着 click：刚跳过来的页面可能有动画/弹窗挡住输入框，
                        #   Locator.click 会一直等到 30 秒超时。
                        #   fill 自己会 focus，直接填更稳。
                        try:
                            await pel.click(timeout=5000)
                        except Exception:
                            pass
                        await pel.fill(PWD, timeout=20000)
                        await page.wait_for_timeout(1200)
                        # ★ 协议也用 Playwright 真实点击
                        _t2 = await click_terms(page)
                        if not _t2:
                            box2 = await page.evaluate("""() => {
                                for (const e of document.querySelectorAll('*')) {
                                    const t = (e.textContent || '').trim();
                                    if (t.startsWith('I have read') && t.length < 150) {
                                        let row = e;
                                        for (let i = 0; i < 3 && row; i++) {
                                            for (const b of row.querySelectorAll('button')) {
                                                const r = b.getBoundingClientRect();
                                                if (r.width >= 10 && r.width <= 24)
                                                    return {x: r.x + r.width/2, y: r.y + r.height/2,
                                                            has_svg: !!b.querySelector('svg')};
                                            }
                                            row = row.parentElement;
                                        }
                                    }
                                }
                                return null;
                            }""")
                            if box2 and not box2.get("has_svg"):
                                await safe_mouse(page, "click", box2["x"], box2["y"])
                                await page.wait_for_timeout(1200)
                        log("%s ⑧ 密码已填 + 协议(%s)" % (tag, "locator" if _t2 else "坐标"))
                        r8 = await page.evaluate("""() => {
                            for (const b of document.querySelectorAll('button')) {
                                const t = (b.innerText||'').trim();
                                if ((t === 'Continue' || t === '继续' || t === 'Sign up' ||
                                     t === 'Create account') && !b.disabled) {
                                    const r = b.getBoundingClientRect();
                                    if (r.width > 5) return {x: r.x + r.width/2, y: r.y + r.height/2};
                                }
                            }
                            return null;
                        }""")
                        if r8:
                            # ★ 关键：等 reCAPTCHA 的 textarea 就位（实测 token 的 value
                            #   一直是空的，等它没意义，等 textarea 在就行）
                            for _w in range(10):
                                try:
                                    ta = await page.evaluate(
                                        """() => !!document.querySelector('textarea[name="g-recaptcha-response"]')""")
                                    if ta:
                                        break
                                except Exception:
                                    pass
                                await page.wait_for_timeout(2000)
                            # ★ 提交先用 DOM click：按钮可能滚动过，按缓存坐标点会点空
                            _clk = await click_btn(
                                page, ["Continue", "继续", "Sign up", "Create account"],
                                near="input[type=password]")
                            _clk = _clk.get("hit") or "无"
                            await page.wait_for_timeout(5000)
                            _st2 = await page_form(page)
                            if not (_st2.get("code") or _st2.get("email")):
                                # 没前进 → 再补一次真实鼠标点击
                                await safe_mouse(page, "move", r8["x"], r8["y"], steps=5)
                                await page.wait_for_timeout(300)
                                await safe_mouse(page, "click", r8["x"], r8["y"])
                                log("%s ⑧ 提交密码 dom=%s → 补鼠标点击" % (tag, _clk))
                            else:
                                log("%s ⑧ 提交密码 dom=%s" % (tag, _clk))
                        await page.wait_for_timeout(10000)
                except Exception as e:
                    log("%s ⑧ %s" % (tag, repr(e)[:50]))

                # ⑨ 验证码
                code_sel = None
                for j in range(12):
                    # ★ 关键：先确认 reCAPTCHA 就绪（否则前端不渲染下一步）
                    try:
                        rcst = await page.evaluate("""() => ({
                            obj: typeof window.grecaptcha,
                            scripts: [...document.querySelectorAll('script[src*="recaptcha"]')].length,
                            ta: !!document.querySelector('textarea[name="g-recaptcha-response"]')
                        })""")
                        if j % 3 == 0:
                            log("%s    reCAPTCHA: %s" % (tag, json.dumps(rcst)))
                    except Exception:
                        pass

                    for s in ['input[placeholder*="verification" i]',
                              "input[inputmode='numeric']",
                              "input[placeholder*='code' i]"]:
                        try:
                            loc = page.locator(s).first
                            if await loc.count() > 0 and await loc.is_visible():
                                code_sel = s
                                break
                        except Exception:
                            pass
                    if code_sel:
                        break
                    # ★ reCAPTCHA 未初始化 → 主动等一下
                    try:
                        if rcst.get("scripts", 0) == 0:
                            await page.wait_for_timeout(1500)
                    except Exception:
                        pass
                    await page.wait_for_timeout(3000)

                if not code_sel:
                    # ★ 页面退回了登录页 → 重试一次提交
                    try:
                        body_txt = await page.evaluate("()=>document.body.innerText.slice(0,120)")
                    except Exception:
                        body_txt = ""
                    log("%s ⑨ ✗ 没验证码框 → %s" % (tag, body_txt[:80]))
                    # 重试：重新走一遍密码提交
                    try:
                        pel2 = page.locator("input[type=password]").first
                        if await pel2.count() > 0 and await pel2.is_visible():
                            await pel2.fill(PWD)
                            await page.wait_for_timeout(1500)
                            rr = await page.evaluate("""() => {
                                for (const b of document.querySelectorAll('button')) {
                                    const t = (b.innerText||'').trim();
                                    if ((t === 'Continue' || t === '继续') && !b.disabled)
                                        return {x: b.getBoundingClientRect().x + 10,
                                                y: b.getBoundingClientRect().y + 10};
                                }
                                return null;
                            }""")
                            if rr:
                                # ★ DOM click 优先，鼠标点击兜底（坐标可能已经滚动失效）
                                _rc = await click_btn(
                                    page, ["Continue", "继续"],
                                    near="input[type=password]")
                                await page.wait_for_timeout(3000)
                                _st3 = await page_form(page)
                                if not _st3.get("code"):
                                    await safe_mouse(page, "click", rr["x"], rr["y"])
                                log("%s ⑨ 重试提交密码 (dom=%s)" % (tag, _rc))
                                for _k in range(8):
                                    await page.wait_for_timeout(3000)
                                    try:
                                        loc2 = page.locator('input[placeholder*="verification" i]').first
                                        if await loc2.count() > 0 and await loc2.is_visible():
                                            code_sel = 'input[placeholder*="verification" i]'
                                            break
                                    except Exception:
                                        pass
                    except Exception:
                        pass
                if not code_sel:
                    # ★ 标记这个出口为坏（key 用节点名，和 take_proxy 对齐）
                    try:
                        st2 = json.load(open(PX_STATE, encoding="utf-8"))
                        _k2 = str(px.get("node") or px.get("port") or px.get("ip") or "")
                        st2.setdefault(_k2, {})["bad"] = 1
                        st2[_k2]["ts"] = time.time()
                        json.dump(st2, open(PX_STATE, "w", encoding="utf-8"), indent=1)
                    except Exception:
                        pass
                    continue
                log("%s ⑨ 验证码框: %s" % (tag, code_sel))

                code = await asyncio.to_thread(read_code, email, 240)
                if not code:
                    log("%s ✗ 没收到码" % tag)
                    continue
                log("%s ⑩ 收到码 %s" % (tag, code))

                el2 = page.locator(code_sel).first
                try:
                    await el2.click(timeout=5000)
                except Exception:
                    pass
                await el2.fill(code, timeout=20000)
                await page.wait_for_timeout(1000)
                # ★ 验证码页也有一个条款框，而且换页后是**重置**的：
                #   不勾的话提交验证码会被静默拒绝（页面停在 Verify your email），
                #   接口还返回 200，看不出任何异常。
                await check_terms(page)
                r11 = await page.evaluate("""() => {
                    for (const b of document.querySelectorAll('button')) {
                        const t = (b.innerText||'').trim();
                        if ((t === 'Continue' || t === '继续' || t === 'Verify' ||
                             t === 'Submit') && !b.disabled) {
                            const r = b.getBoundingClientRect();
                            if (r.width > 5) return {x: r.x + r.width/2, y: r.y + r.height/2};
                        }
                    }
                    return null;
                }""")
                if r11:
                    # ★ DOM click 优先：填完验证码页面可能滚动过，缓存坐标会点空
                    _ck = await click_btn(
                        page, ["Continue", "继续", "Verify", "Submit"], near=code_sel)
                    await page.wait_for_timeout(4000)
                    try:
                        _tk = await page.evaluate("() => localStorage.getItem('_token')")
                    except Exception:
                        _tk = None
                    if not _tk:
                        # 没出 token → 补一次真实鼠标点击
                        await safe_mouse(page, "move", r11["x"], r11["y"], steps=5)
                        await page.wait_for_timeout(300)
                        await safe_mouse(page, "click", r11["x"], r11["y"])
                log("%s ⑪ 提交码" % tag)

                # ⑫ token
                token = None
                for j in range(40):
                    await page.wait_for_timeout(2000)
                    try:
                        token = await page.evaluate("() => localStorage.getItem('_token')")
                    except Exception:
                        token = None
                    if token:
                        break
                    if j % 8 == 7:
                        log("%s    …%ds %s" % (tag, (j+1)*2, page.url[:50]))
                    # ★ 30 秒还没出 token → 把码再提一次（页面可能没真的提交）
                    if j == 15:
                        try:
                            _el3 = page.locator(code_sel).first
                            if await _el3.count() > 0 and await _el3.is_visible():
                                await _el3.fill(code)
                                await page.wait_for_timeout(500)
                                await check_terms(page)
                                await click_btn(
                                    page, ["Continue", "继续", "Verify", "Submit"],
                                    near=code_sel)
                                log("%s ⑪ 重提验证码" % tag)
                        except Exception:
                            pass

                if not token:
                    # ★ 诊断：token 到底落在哪儿
                    try:
                        _st4 = await page.evaluate(r"""() => {
                            const ls = [];
                            for (let i = 0; i < localStorage.length; i++) {
                                const k = localStorage.key(i);
                                ls.push(k + '=' + String(localStorage.getItem(k)).slice(0, 18));
                            }
                            const ss = [];
                            for (let i = 0; i < sessionStorage.length; i++) {
                                const k = sessionStorage.key(i);
                                ss.push(k + '=' + String(sessionStorage.getItem(k)).slice(0, 18));
                            }
                            return {
                                url: location.href.slice(0, 90),
                                ls: ls.slice(0, 8),
                                ss: ss.slice(0, 8),
                                cookie: (document.cookie||'').slice(0, 120),
                                body: (document.body.innerText||'').replace(/\s+/g,' ').slice(0, 160)
                            };
                        }""")
                        log("%s ⑫ token 落点诊断: %s" % (tag, json.dumps(_st4, ensure_ascii=False)))
                    except Exception as e:
                        log("%s ⑫ 诊断失败 %s" % (tag, str(e)[:60]))
                if token:
                    ts = time.strftime("%Y-%m-%d %H:%M:%S")
                    with open(OUT, "a", encoding="utf-8") as f:
                        f.write("%s----%s----%s\n" % (email, ts, token))
                    with open(ONLY, "a", encoding="utf-8") as f:
                        f.write(token + "\n")
                    log("%s ★★★ 成功！%s" % (tag, email))
                    ok += 1
                    i += 1
                    tries = 0
                else:
                    log("%s ✗ 没 token（换出口重试 %d/%d）" % (tag, tries, MAX_TRIES))

        except Exception as e:
            log("%s 异常 %s" % (tag, repr(e)[:120]))

        if i < total:
            await asyncio.sleep(8)

    dt = time.time() - t0
    log("")
    log("=" * 62)
    log("  完成！成功 %d / %d   耗时 %.0f 秒" % (ok, total, dt))
    try:
        n = sum(1 for _ in open(OUT, encoding="utf-8"))
        log("  token 文件 %s 共 %d 条" % (OUT, n))
    except Exception:
        pass
    log("=" * 62)


asyncio.run(main())
