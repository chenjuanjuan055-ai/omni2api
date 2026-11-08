#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量探测多出口：出口 IP / 归属 / 能否打 chatgpt.com / 能否过 auth.openai.com 的 CF 校验。

判据很关键
----------
``GET https://chatgpt.com/cdn-cgi/trace`` 返回 200 **不代表能注册** ——
Cloudflare WARP / AWS / 机房 IP 全都能过这个。

真正决定能不能注册的是 auth.openai.com 的 CF 信誉校验：

    GET https://auth.openai.com/oauth/authorize   （缺参数即可）
      400 / 302  -> CF 已放行（缺参数是业务错误）
      403        -> CF 按出口 IP 信誉拦截

用法
----
    python scripts/outlets_probe.py --dir <生成目录> [--workers 48] [--out probe.json]
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import io
import json
import pathlib
import sys

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")

DC_HINTS = (
    "amazon", "aws", "google", "microsoft", "azure", "digitalocean", "linode",
    "akamai", "cloudflare", "vultr", "hetzner", "ovh", "oracle", "leaseweb",
    "hosting", "datacenter", "data center", "cloud", "llc", "inc", "server",
    "limited", "ltd", "network", "solution", "technology", "gmbh", "pvt",
    "datacamp", "hostpapa", "akile", "catixs", "psychz", "peg tech",
)
RESI_HINTS = (
    "comcast", "at&t", "verizon", "spectrum", "charter", "cox", "optimum",
    "centurylink", "frontier", "windstream", "tmobile", "t-mobile", "earthlink",
    "altice", "cable", "wideopenwest", "sonic", "metronet", "starlink",
    "residential", "broadband", "cablevision", "telus", "rogers", "videotron",
    "optus", "vodafone", "deutsche", "unitymedia", "dsl", "fiber", "fibre",
)


def probe(item: dict) -> dict:
    port = int(item["port"])
    px = {"http": f"http://127.0.0.1:{port}", "https": f"http://127.0.0.1:{port}"}
    rec = {"port": port, "name": item.get("name"), "remark": item.get("remark", ""),
           "type": item.get("type", ""), "server": item.get("server", "")}
    s = requests.Session()
    s.trust_env = False
    s.headers.update({"User-Agent": UA})
    try:
        r = s.get("https://ipwho.is/", proxies=px, timeout=15)
        j = r.json()
        if not j.get("success"):
            rec["ip_err"] = str(j.get("message") or j)[:120]
            return rec
        c = j.get("connection") or {}
        rec.update(exit_ip=j.get("ip"), country=j.get("country_code"), city=j.get("city"),
                   isp=c.get("isp") or "", org=c.get("org") or "", asn=c.get("asn"))
    except Exception as e:
        rec["ip_err"] = f"{type(e).__name__}: {e}"[:150]
        return rec

    try:
        r2 = s.get("https://chatgpt.com/cdn-cgi/trace", proxies=px, timeout=15)
        rec["chatgpt"] = r2.status_code
        rec["cg_ok"] = r2.status_code == 200 and "chatgpt.com" in (r2.text or "")
    except Exception as e:
        rec["cg_err"] = f"{type(e).__name__}: {e}"[:120]

    try:
        r3 = s.get("https://auth.openai.com/oauth/authorize", proxies=px, timeout=18,
                   allow_redirects=False)
        rec["auth_status"] = r3.status_code
        rec["cf_pass"] = r3.status_code != 403
    except Exception as e:
        rec["auth_err"] = f"{type(e).__name__}: {e}"[:120]
    s.close()
    return rec


def classify(rec: dict) -> str:
    if not rec.get("exit_ip"):
        return "dead"
    blob = f"{rec.get('isp', '')} {rec.get('org', '')}".lower()
    if any(h in blob for h in DC_HINTS):
        return "dc"
    if any(h in blob for h in RESI_HINTS):
        return "resi"
    return "unknown"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="生成目录（含 ports.json）")
    ap.add_argument("--workers", type=int, default=48)
    ap.add_argument("--out", default="probe.json")
    args = ap.parse_args()

    d = pathlib.Path(args.dir)
    meta = json.loads((d / "ports.json").read_text(encoding="utf-8"))

    res = []
    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        for i, rec in enumerate(ex.map(probe, meta)):
            rec["kind"] = classify(rec)
            res.append(rec)
            if (i + 1) % 50 == 0:
                print(f"  {i + 1}/{len(meta)}", flush=True)

    res.sort(key=lambda r: r["port"])
    out = d / args.out
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")

    alive = [r for r in res if r.get("exit_ip")]
    cg = [r for r in alive if r.get("cg_ok")]
    passed = [r for r in alive if r.get("cf_pass")]
    print(f"\n总 {len(res)}  活 {len(alive)}  能打 chatgpt {len(cg)}  过 auth CF {len(passed)}")
    kinds: dict[str, int] = {}
    for r in alive:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
    print("分类:", kinds)
    print("\n=== 过 auth.openai.com CF 的出口 ===")
    for r in sorted(passed, key=lambda x: x["port"]):
        print(f"  {r['port']}\t{r.get('exit_ip', ''):<16}\t{r.get('country')}\t{r['kind']:8}"
              f"\t{(r.get('isp') or '')[:26]:<26}{r['remark'][:40]}")
    print(f"\n结果 -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
