#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 v2rayN 的节点库转成"一节点一本地端口"的 mihomo 多出口配置。

背景
----
v2rayN 的节点库在 ``guiConfigs/guiNDB.db`` 的 ``ProfileItem`` 表里，可以直接生成
mihomo 多出口配置（每个节点一个本地 HTTP 端口），供注册机/反代轮换出口。

踩过的坑（都在这里处理掉了）
---------------------------
1. ``Flow`` / ``SsMethod`` / ``SalamanderPass`` 藏在 ``ProtoExtra`` 字段的 JSON 里，
   不是独立列。不解析 ⇒ reality 节点缺 ``flow: xtls-rprx-vision`` ⇒ 全部握手失败。
2. ``ConfigType`` 不能一律当 vless：
   1=VMess 3=Shadowsocks 5=VLESS 6=Trojan 7=Hysteria2 11=AnyTLS。
3. REALITY ``short-id`` 必须是偶数位 hex、长度 ≤16，**且不能是空串**
   （空串会让 mihomo 启动即 fatal：``proxy N: invalid REALITY short ID``）。
4. 节点名会重名（多个 ``US 🇺🇸``）⇒ mihomo 拒绝启动，需加唯一后缀。

用法
----
    python scripts/outlets_gen_from_v2rayn.py --db <guiNDB.db> --out <目录> --base-port 24100
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sqlite3

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

# 占位/说明性节点（套餐信息、频道通知等），Address 通常是 0.0.0.0
JUNK = re.compile(r"每日更新|电报频道|套餐到期|剩余流量|距离下次重置|官网|订阅|客服|公告|更新订阅|到期")

# v2rayN EConfigType
CT_VMESS, CT_SS, CT_VLESS, CT_TROJAN, CT_HYSTERIA2, CT_ANYTLS = 1, 3, 5, 6, 7, 11

# short-id 校验：偶数位 hex、长度 ≤16（空串非法，必须整条省略）
HEX_SHORT_ID = re.compile(r"(?:[0-9a-fA-F]{2}){1,8}")

# 美国住宅关键词（仅用于排序优先级，不影响能否生成）
US_RESI = re.compile(
    r"美国.*(家宽|家庭宽带|住宅|ATT|Frontier|Cox|Verizon|Wave|Spectrum|Charter)|"
    r"(家宽|家庭宽带|住宅).*美国|家宽IP|双ISP家宽|极纯家宽", re.I)
US_ANY = re.compile(r"美国|🇺🇸|\bUS\b|洛杉矶|圣何塞|西雅图|芝加哥|纽约|ATT|Frontier|Cox|Verizon", re.I)


def _extra(row: dict) -> dict:
    """解析 ProtoExtra（Flow / SsMethod / SalamanderPass 都在里面）。"""
    try:
        return json.loads(row.get("ProtoExtra") or "{}")
    except Exception:
        return {}


def _build(row: dict, idx: int) -> dict | None:
    addr = str(row.get("Address") or "").strip()
    try:
        port = int(row.get("Port") or 0)
    except Exception:
        return None
    if not addr or not port or addr in ("0.0.0.0", "127.0.0.1"):
        return None

    pwd = str(row.get("Password") or "").strip()
    ct = int(row.get("ConfigType") or 0)
    pe = _extra(row)
    sni = str(row.get("Sni") or "").strip()
    net = (str(row.get("Network") or "tcp").strip() or "tcp").lower()
    if net == "raw":
        net = "tcp"
    sec = str(row.get("StreamSecurity") or "").strip().lower()
    fp = str(row.get("Fingerprint") or "").strip()
    allow_insecure = str(row.get("AllowInsecure") or "").lower() == "true"

    base = {
        "name": f"[{idx:03d}] {str(row.get('Remarks') or addr)[:58]}",
        "server": addr,
        "port": port,
        "udp": True,
    }

    if ct == CT_VLESS:
        if not pwd:
            return None
        p = dict(base)
        p.update(type="vless", uuid=pwd, network=net, tls=sec in ("tls", "reality"))
        if sni:
            p["servername"] = sni
        if fp:
            p["client-fingerprint"] = fp
        flow = str(pe.get("Flow") or "").strip()
        if flow and net == "tcp" and p["tls"]:
            p["flow"] = flow
        if sec == "reality":
            pk = str(row.get("PublicKey") or "").strip()
            sid = str(row.get("ShortId") or "").strip()
            if not pk or not HEX_SHORT_ID.fullmatch(sid):
                return None
            p["reality-opts"] = {"public-key": pk, "short-id": sid}
        if net == "ws":
            p["ws-opts"] = {
                "path": str(row.get("Path") or "/") or "/",
                "headers": {"Host": str(row.get("RequestHost") or sni or addr)},
            }
        elif net == "grpc":
            p["grpc-opts"] = {"grpc-service-name": str(row.get("Path") or "")}
        if allow_insecure:
            p["skip-cert-verify"] = True
        return p

    if ct == CT_SS:
        cipher = str(pe.get("SsMethod") or "").strip()
        if not pwd or not cipher:
            return None
        p = dict(base)
        p.update(type="ss", password=pwd, cipher=cipher)
        if allow_insecure:
            p["skip-cert-verify"] = True
        return p

    if ct == CT_HYSTERIA2:
        if not pwd:
            return None
        p = dict(base)
        p.update(type="hysteria2", password=pwd)
        if sni:
            p["sni"] = sni
        salam = str(pe.get("SalamanderPass") or "").strip()
        if salam:
            p.update(obfs="salamander", **{"obfs-password": salam})
        hop = str(pe.get("Ports") or "").strip()
        if hop:
            p["ports"] = hop
        if allow_insecure:
            p["skip-cert-verify"] = True
        return p

    if ct == CT_ANYTLS:
        if not pwd:
            return None
        p = dict(base)
        p.update(type="anytls", password=pwd)
        if sni:
            p["sni"] = sni
        if allow_insecure:
            p["skip-cert-verify"] = True
        return p

    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="v2rayN 节点库 -> mihomo 多出口")
    ap.add_argument("--db", required=True, help="guiNDB.db 路径")
    ap.add_argument("--out", required=True, help="输出目录")
    ap.add_argument("--base-port", type=int, default=24100, help="起始本地端口")
    ap.add_argument("--mixed-port", type=int, default=24000, help="mihomo mixed-port")
    ap.add_argument("--controller-port", type=int, default=24001, help="控制端口")
    args = ap.parse_args()

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute("SELECT * FROM ProfileItem")]
    con.close()

    cand = [r for r in rows if not JUNK.search(str(r.get("Remarks") or ""))]

    def prio(r: dict) -> int:
        rm = str(r.get("Remarks") or "")
        if US_RESI.search(rm):
            return 0
        if US_ANY.search(rm):
            return 1
        return 2

    cand.sort(key=prio)

    proxies, seen = [], set()
    for r in cand:
        p = _build(r, len(proxies))
        if not p:
            continue
        key = (p["server"], p["port"], p.get("uuid") or p.get("password"))
        if key in seen:
            continue
        seen.add(key)
        proxies.append(p)

    listeners = [
        {"name": f"out-{args.base_port + i}", "type": "mixed",
         "port": args.base_port + i, "listen": "127.0.0.1", "proxy": p["name"]}
        for i, p in enumerate(proxies)
    ]

    cfg = {
        "mixed-port": args.mixed_port,
        "allow-lan": False,
        "mode": "rule",
        "log-level": "warning",
        "ipv6": False,
        "external-controller": f"127.0.0.1:{args.controller_port}",
        "dns": {
            "enable": True, "ipv6": False, "listen": f"127.0.0.1:{args.controller_port + 52}",
            "enhanced-mode": "fake-ip", "fake-ip-range": "198.18.0.1/16",
            "default-nameserver": ["223.5.5.5", "119.29.29.29"],
            "nameserver": ["https://dns.alidns.com/dns-query", "223.5.5.5"],
            "proxy-server-nameserver": ["https://dns.alidns.com/dns-query"],
        },
        "proxies": proxies,
        "listeners": listeners,
        "rules": ["MATCH,DIRECT"],
    }

    if yaml is None:
        raise SystemExit("需要 pyyaml：pip install pyyaml")
    (out / "config.yaml").write_text(
        "# generated by scripts/outlets_gen_from_v2rayn.py\n"
        + yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    meta = [{
        "port": args.base_port + i,
        "name": p["name"],
        "type": p["type"],
        "server": p["server"],
        "sni": p.get("servername") or p.get("sni") or "",
        "remark": p["name"][5:],
    } for i, p in enumerate(proxies)]
    (out / "ports.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"节点库 {len(rows)}，去占位 {len(cand)}，生成 {len(proxies)}")
    print(f"配置 -> {out / 'config.yaml'}")
    print(f"端口 {args.base_port} - {args.base_port + len(listeners) - 1}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
