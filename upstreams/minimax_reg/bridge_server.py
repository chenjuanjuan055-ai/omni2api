"""bridge_server.py - 本地桥接服务（扩展与后端之间的中转）。

扩展在普通 Chrome 里跑（无 --remote-debugging-port，腾讯看不到自动化信号），
它需要两件外部信息：
    1. 一个新邮箱（DuckMail 建号）
    2. 把拿到的 _token 交回来存盘

本服务就干这两件事，监听 127.0.0.1:8791。

接口：
    POST /mail         -> {"email":"...","pwd":"...","tok":"...","mailbox":"..."}
    POST /token        -> {"email":"...","token":"..."}   落盘
    GET  /health       -> {"ok":true}
"""
import json
import os
import random
import re
import string
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

import os

DUCK = os.environ.get("MMX_DUCK_API", "https://api.duckmail.sbs")
# DuckMail 主密钥只在环境变量 / 控制台配置里给，不进仓库
MASTER = os.environ.get("MMX_DUCKMASTER", "")
DOMAIN = "niceground.shop"
# ★ 域名池轮换: 同一域名用多了会被 MiniMax 风控（收不到码）
#   轮换这 19 个可用域名中的几个
DOMAINS = [
    "niceground.shop",
    "stoneground.shop",
    "lakeground.shop",
    "canvaspace.shop",
    "vercelspace.shop",
    "seedancespace.shop",
    "happyhorsespace.shop",
    "bananaspace.shop",
    "sunstarmoon.shop",
    "moonstarsun.shop",
    "sunmoonlight.shop",
    "hubaiclass.org",
    "markaihub.shop",
    "markstonehub.org",
    "glasswhitehub.com",
]
_domain_i = [0]   # 轮换游标
_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
try:
    os.makedirs(_DATA_DIR, exist_ok=True)
except Exception:
    pass
OUT = os.environ.get("MMX_TOKEN_FILE", os.path.join(_DATA_DIR, "minimaxtoken.txt"))
ONLY = os.environ.get("MMX_TOKEN_ONLY_FILE", os.path.join(_DATA_DIR, "minimaxtoken_only.txt"))
PORT = int(os.environ.get("MMX_BRIDGE_PORT", "8791"))

_mails = {}      # email -> {pwd, tok, ts}


def dk(method, path, body=None, token=None, timeout=30):
    h = {"Authorization": f"Bearer {token or MASTER}", "Content-Type": "application/json"}
    r = requests.request(method, DUCK + path, headers=h,
                         data=json.dumps(body) if body else None, timeout=timeout)
    r.raise_for_status()
    return r.json()


def mint(dom=None):
    tag = "mx" + "".join(random.choices(string.digits, k=8))
    pwd = "Aa" + "".join(random.choices(string.ascii_letters + string.digits, k=10)) + "!"
    # ★ 指定域名优先，否则轮换
    if dom:
        d = dom
    else:
        d = DOMAINS[_domain_i[0] % len(DOMAINS)]
        _domain_i[0] += 1
    email = f"{tag}@{d}"
    try:
        dk("POST", "/accounts", {"address": email, "password": pwd})
    except Exception:
        pass
    tok = dk("POST", "/token", {"address": email, "password": pwd})["token"]
    _mails[email] = {"pwd": pwd, "tok": tok, "ts": time.time()}
    # 落盘，免得进程重启后丢失
    try:
        with open(os.path.join(_DATA_DIR, "mails_store.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps({"email": email, "pwd": pwd, "tok": tok,
                                "ts": time.strftime("%Y-%m-%d %H:%M:%S")},
                               ensure_ascii=False) + "\n")
    except Exception:
        pass
    return {"email": email, "pwd": pwd, "tok": tok}


def read_code(mail_tok, timeout=150):
    end = time.time() + timeout
    seen = set()
    while time.time() < end:
        try:
            msgs = dk("GET", "/messages", token=mail_tok, timeout=20).get("hydra:member", [])
        except Exception:
            msgs = []
        for m in msgs:
            mid = m.get("id")
            if mid in seen:
                continue
            seen.add(mid)
            txt = (m.get("subject", "") + " " + m.get("text", ""))
            mt = re.search(r"(\d{4,8})", txt)
            if mt and re.search(r"verif|code|验证", txt, re.I):
                return mt.group(1)
        time.sleep(3)
    return None


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_OPTIONS(self):
        self._send({"ok": True})

    def do_GET(self):
        if self.path.startswith("/health"):
            return self._send({"ok": True, "mails": len(_mails)})
        if self.path.startswith("/mails"):
            # 列出所有已建邮箱（含密码，本机自用）
            return self._send({em: {"pwd": v["pwd"], "tok": v["tok"]}
                               for em, v in _mails.items()})
        if self.path.startswith("/mail/"):
            # 查验证码: /mail/<urlenc email>
            import urllib.parse as up
            em = up.unquote(self.path.split("/mail/", 1)[1])
            m = _mails.get(em)
            if not m:
                return self._send({"error": "unknown email"}, 404)
            code = read_code(m["tok"], timeout=120)
            return self._send({"email": em, "code": code})
        return self._send({"error": "not found"}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n) if n else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            body = {}

        if self.path.startswith("/mail"):
            try:
                m = mint(body.get("domain"))
                print(f"[+] 新邮箱 {m['email']}", flush=True)
                return self._send(m)
            except Exception as e:
                return self._send({"error": repr(e)[:120]}, 500)

        if self.path.startswith("/code"):
            em = body.get("email")
            m = _mails.get(em)
            if not m:
                return self._send({"error": "unknown email"}, 404)
            code = read_code(m["tok"], timeout=int(body.get("timeout", 150)))
            print(f"[*] {em} 验证码 {code}", flush=True)
            return self._send({"email": em, "code": code})

        if self.path.startswith("/token"):
            em = body.get("email", "?")
            tk = body.get("token", "")
            if not tk:
                return self._send({"error": "no token"}, 400)
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            with open(OUT, "a", encoding="utf-8") as f:
                f.write(f"{em}----{ts}----{tk}\n")
            with open(ONLY, "a", encoding="utf-8") as f:
                f.write(tk + "\n")
            print(f"[★] TOKEN 已存 {em}  {tk[:40]}…", flush=True)
            return self._send({"ok": True, "saved": OUT})

        if self.path.startswith("/log"):
            print(f"[扩展] {body.get('msg','')[:200]}", flush=True)
            return self._send({"ok": True})

        return self._send({"error": "not found"}, 404)


if __name__ == "__main__":
    if not MASTER:
        print("! 缺少 DuckMail 主密钥：请设置环境变量 MMX_DUCKMASTER")
        raise SystemExit(2)
    print(f"桥接服务 http://127.0.0.1:{PORT}")
    print(f"  POST /mail    建邮箱")
    print(f"  POST /code    {{email}} 取验证码")
    print(f"  POST /token   {{email,token}} 交给尾")
    print(f"  GET  /health")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
