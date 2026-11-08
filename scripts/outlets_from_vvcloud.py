#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从「站点注册控制台」(19879) 的 VVCloud 账号拉订阅，生成住宅 IP 多出口 mihomo 配置。

背景
----
VVCloud 试用账号（注册即送 24h / 1GB）本身就是**住宅 IP 机场**，订阅里带
``专线A1-美国*-家宽IP`` 这类落地在美国家宽的节点。

关键坑
------
1. **节点密码按账号绑定**：同一个 ``df.668fx.com:56512`` 端口，不同订阅账号
   下发的密码不同。拿 A 账号的订阅去连 B 账号的节点 = 握手直接被 RST。
   ⇒ 必须用**同一个账号**的订阅生成配置。
2. 试用订阅会带一堆信息节点（"✅付费后可用全部线路"、"剩余流量…"），
   它们不是真节点，要过滤掉。
3. ``site-console`` 自己的代理配置是 ``mode: global`` 但**没建 GLOBAL 代理组**，
   结果流量全部直连（出口变成本机 IP）。自己生成配置时要给 listener 显式绑 ``proxy``。

用法
----
    python scripts/outlets_from_vvcloud.py --console http://127.0.0.1:19879 \
        --out <目录> --base-port 28100
"""
from __future__ import annotations

import argparse
import base64
import json
import pathlib
import re
import sys
import urllib.parse

import requests

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

INFO_NAME_RE = re.compile(r"剩余流量|套餐到期|距离下次|到期时间|过期时间|官网|续费|重置|有效|付费后可用")


def _b64dec(s: str) -> str:
    return base64.b64decode(s + "=" * (-len(s) % 4)).decode("utf-8", "replace")


def parse_subscription(text: str) -> list[dict]:
    """订阅原文 -> 节点列表（兼容 ss/vmess/vless/trojan/anytls/hysteria2）。"""
    text = text.strip()
    if not text.startswith(("ss://", "vmess://", "vless://", "trojan://",
                            "anytls://", "hysteria2://", "hy2://", "#")):
        try:
            text = _b64dec(text)
        except Exception:
            pass
    nodes = []
    for line in text.splitlines():
        line = line.strip()
        if not line or "://" not in line:
            continue
        proto, rest = line.split("://", 1)
        proto = proto.lower()
        name = urllib.parse.unquote(rest.split("#", 1)[1]) if "#" in rest else f"{proto}-{len(nodes)}"
        body = rest.split("#", 1)[0]
        try:
            if proto == "ss":
                ui, hostport = body.rsplit("@", 1)
                host, port = hostport.rsplit(":", 1)
                try:
                    dec = _b64dec(ui)
                except Exception:
                    dec = urllib.parse.unquote(ui)
                method, password = dec.split(":", 1)
                nodes.append({"proto": proto, "name": name, "server": host,
                              "port": int(port.split("?")[0]), "cipher": method, "password": password})
            elif proto == "vmess":
                j = json.loads(_b64dec(body))
                nodes.append({"proto": proto, "name": j.get("ps") or name, "server": j.get("add"),
                              "port": int(j.get("port") or 0), "uuid": j.get("id"),
                              "aid": int(j.get("aid") or 0), "net": j.get("net") or "tcp",
                              "tls": str(j.get("tls")).lower() == "tls",
                              "sni": j.get("sni") or j.get("host") or "", "fp": j.get("fp") or "chrome"})
            elif proto in ("vless", "trojan", "anytls", "hysteria2", "hy2"):
                ui, hostport = body.split("@", 1)
                host, port = hostport.rsplit(":", 1)
                q = urllib.parse.parse_qs(hostport.split("?", 1)[1] if "?" in hostport else "")
                g = lambda k, d="": (q.get(k) or [d])[0]  # noqa: E731
                n = {"proto": proto, "name": name, "server": host, "port": int(port)}
                if proto == "vless":
                    n.update(uuid=ui, net=g("type", "tcp"), sni=g("sni") or g("servername"),
                             flow=g("flow"), pbk=g("pbk"), sid=g("sid"), fp=g("fp", "chrome"),
                             security=g("security", "tls"))
                else:
                    n.update(password=urllib.parse.unquote(ui), sni=g("sni") or g("peer"),
                             insecure=g("allowInsecure", g("insecure", "0")) == "1")
                nodes.append(n)
        except Exception:
            continue
    return nodes


def to_mihomo(nodes: list[dict]) -> list[dict]:
    proxies = []
    for i, n in enumerate(nodes):
        nm = f"{i:03d}|{n['name'][:54]}"
        try:
            if n["proto"] == "ss":
                proxies.append({"name": nm, "type": "ss", "server": n["server"], "port": n["port"],
                                "cipher": n["cipher"], "password": n["password"], "udp": True})
            elif n["proto"] == "vmess":
                p = {"name": nm, "type": "vmess", "server": n["server"], "port": n["port"],
                     "uuid": n["uuid"], "alterId": n["aid"], "cipher": "auto",
                     "network": n["net"], "udp": True, "client-fingerprint": n["fp"]}
                if n["tls"]:
                    p["tls"] = True
                    if n["sni"]:
                        p["servername"] = n["sni"]
                proxies.append(p)
            elif n["proto"] == "vless":
                p = {"name": nm, "type": "vless", "server": n["server"], "port": n["port"],
                     "uuid": n["uuid"], "network": n["net"], "udp": True,
                     "client-fingerprint": n["fp"]}
                if n.get("flow"):
                    p["flow"] = n["flow"]
                if n.get("security") == "reality":
                    p["tls"] = True
                    if n.get("sni"):
                        p["servername"] = n["sni"]
                    p["reality-opts"] = {"public-key": n.get("pbk") or ""}
                    if n.get("sid"):
                        p["reality-opts"]["short-id"] = n["sid"]
                elif n.get("security") == "tls":
                    p["tls"] = True
                    if n.get("sni"):
                        p["servername"] = n["sni"]
                proxies.append(p)
            elif n["proto"] == "trojan":
                proxies.append({"name": nm, "type": "trojan", "server": n["server"], "port": n["port"],
                                "password": n["password"], "sni": n["sni"],
                                "skip-cert-verify": n["insecure"], "udp": True})
            elif n["proto"] == "anytls":
                proxies.append({"name": nm, "type": "anytls", "server": n["server"], "port": n["port"],
                                "password": n["password"], "sni": n["sni"],
                                "skip-cert-verify": n["insecure"],
                                "client-fingerprint": "chrome", "udp": True})
            elif n["proto"] in ("hysteria2", "hy2"):
                proxies.append({"name": nm, "type": "hysteria2", "server": n["server"], "port": n["port"],
                                "password": n["password"], "sni": n["sni"],
                                "skip-cert-verify": n["insecure"], "udp": True})
        except Exception:
            continue
    return proxies


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--console", default="http://127.0.0.1:19879", help="站点注册控制台地址")
    ap.add_argument("--out", required=True, help="输出目录")
    ap.add_argument("--base-port", type=int, default=28100)
    ap.add_argument("--email", default="", help="指定用哪个账号的订阅（默认全部）")
    ap.add_argument("--ua", default="v2rayN/6.0")
    args = ap.parse_args()

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    s = requests.Session()
    s.trust_env = False

    accounts = s.get(f"{args.console}/api/accounts", timeout=30).json().get("data") or []
    if args.email:
        accounts = [a for a in accounts if a.get("email") == args.email]
    print(f"控制台账号 {len(accounts)}")

    all_meta = []
    for a in accounts:
        url = a.get("subscribe_url")
        if not url:
            continue
        try:
            r = s.get(url, headers={"User-Agent": args.ua}, timeout=30)
            text = r.text or ""
        except Exception as e:
            print(f"  {a.get('email')} 拉取失败: {type(e).__name__}")
            continue
        nodes = [n for n in parse_subscription(text) if not INFO_NAME_RE.search(n["name"])]
        proxies = to_mihomo(nodes)
        print(f"  {a.get('email'):<40} 有效节点 {len(proxies)}")
        for i, p in enumerate(proxies):
            all_meta.append({"account": a.get("email"), "base": args.base_port + len(all_meta),
                             "idx": i, "proxy": p})

    if not all_meta:
        raise SystemExit("没有可用节点")

    proxies = [m["proxy"] for m in all_meta]
    for i, p in enumerate(proxies):
        p["name"] = f"{i:03d}|{all_meta[i]['account'].split('@')[0][:18]}|{all_meta[i]['proxy']['name'].split('|', 1)[-1][:40]}"
    listeners = [{"name": f"vv-{all_meta[i]['base']}", "type": "mixed",
                  "port": all_meta[i]["base"], "listen": "127.0.0.1", "proxy": p["name"]}
                 for i, p in enumerate(proxies)]

    cfg = {
        "mixed-port": args.base_port - 100, "allow-lan": False, "mode": "rule",
        "log-level": "warning", "ipv6": False,
        "external-controller": f"127.0.0.1:{args.base_port - 99}",
        "dns": {"enable": True, "ipv6": False, "listen": f"127.0.0.1:{args.base_port - 47}",
                "nameserver": ["223.5.5.5", "119.29.29.29"]},
        "proxies": proxies, "listeners": listeners, "rules": ["MATCH,DIRECT"],
    }
    if yaml is None:
        raise SystemExit("需要 pyyaml")
    (out / "config.yaml").write_text(
        "# generated by scripts/outlets_from_vvcloud.py\n"
        + yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
    meta = [{"port": all_meta[i]["base"], "name": p["name"], "type": p["type"],
             "server": p["server"], "account": all_meta[i]["account"],
             "remark": p["name"].split("|", 2)[-1]} for i, p in enumerate(proxies)]
    (out / "ports.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n生成 {len(proxies)} 个，端口 {args.base_port}-{args.base_port + len(proxies) - 1}")
    for m in meta:
        print(f"  {m['port']}\t{m['type']:9}\t{m['account'].split('@')[0][:22]:22}\t{m['remark'][:44]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
