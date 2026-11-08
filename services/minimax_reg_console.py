# -*- coding: utf-8 -*-
"""MiniMax 自动注册的控制台编排层。

注册机本体在 ``upstreams/minimax_reg``（vendored 的 miniMax 自动注册脚本 +
邮箱桥接服务）。这里只做三件事：

1. 起/停邮箱桥接（子进程，127.0.0.1:<bridge_port>）
2. 起注册任务（子进程，跑 N 轮，每轮注册一个账号）
3. 读回产出的 token 供控制台展示

依赖说明：注册机用 camoufox（反检测浏览器），装在独立的 Python 里，
所以这里统一用**子进程**跑，不要求主服务环境里有这些依赖。

配置在 config.json 的 ``minimax_reg`` 段；敏感项（DuckMail 主密钥）不进仓库。
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from services.config import config

REPO_ROOT = Path(__file__).resolve().parent.parent
REG_DIR = REPO_ROOT / "upstreams" / "minimax_reg"
DATA_DIR = REG_DIR / "data"

POOL_DIR = DATA_DIR / "mihomo_pool"
POOL_CFG = POOL_DIR / "config.yaml"
POOL_STATE = POOL_DIR / "pool.json"
PX_PORTS = DATA_DIR / "px_ports.json"
PX_LIVE = DATA_DIR / "px_live.json"

MIHOMO_BIN = r"D:\xi\Ai\smart-proxy\mihomo.exe"
POOL_MIXED_PORT = 7896
POOL_CTRL_PORT = 9098

# 分流：google/recaptcha 走本机 v2rayN，其余走池子节点
V2RAY_PORT = 10808
V2RAY_PROXY_NAME = "V2RAY-LOCAL"
SPLIT_GOOGLE = False   # 见 _render_pool_cfg 的说明

# ★ 本机其它代理工具的本地端口（当额外出口用）：
#   它们和机场节点的 IP 段完全不同，目标站往往只封了其中一批。
#   20200 = Clash Verge 的 mixed-port（23.237.50.x 高速线路，实测能用）
#   10808 = v2rayN 的 mixed-port
EXTRA_EXIT_PORTS = [20200, 20201, 10808, 8099]
MINIMAX_DOMAINS = [
    "minimax.io", "minimaxi.com", "hailuoai.com", "hailuo.ai",
    "minimax.chat", "minimax.io.cn",
]
GOOGLE_DOMAINS = [
    "google.com", "gstatic.com", "recaptcha.net", "googleapis.com",
    "google-analytics.com", "googletagmanager.com", "doubleclick.net",
    "googleads.g.doubleclick.net", "recaptchaenterprise.com",
]

# ★ 出口池源配置候选：需要是 mihomo 配置，且带 proxies（带 listeners 更好）。
#   没有 listeners 的会在这里自动合成"一节点一端口"。
POOL_SRC_CANDIDATES = [
    r"D:\xi\tmp_9router\vv-proxy\config.yaml",
    r"D:\xi\Documents\Hermes\minimax_reg\sweep_pool.yaml",
    # ★ 精品高速线路（Clash Verge 在用的那套，IP 段质量明显好于机场节点）
    r"D:\反代项目集\05_新项目\_mihomo\residential_1031.yaml",
]

DEFAULT_PYTHON = r"C:\Users\xi\AppData\Local\Programs\Python\Python312\python.exe"

DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "python": DEFAULT_PYTHON,
    "bridge_port": 18791,
    "proxy": "http://127.0.0.1:10808",
    "duck_api": "https://api.duckmail.sbs",
    "duck_master": "",
    "password": "Aa123456!",
    "start_url": "https://agent.minimax.io",
    "pool_config": "",
}


class MinimaxRegError(RuntimeError):
    pass


class MinimaxRegConsole:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._bridge: subprocess.Popen | None = None
        self._state: dict[str, Any] = {
            "running": False,
            "total": 0,
            "done": 0,
            "ok": 0,
            "log": [],
            "started_at": 0,
            "finished_at": 0,
            "current": "",
        }

    # ------------------------------------------------------------------ 配置
    def settings(self) -> dict[str, Any]:
        raw = config.data.get("minimax_reg")
        raw = raw if isinstance(raw, dict) else {}
        out = dict(DEFAULTS)
        for k, v in raw.items():
            if v not in (None, ""):
                out[k] = v
        # 环境变量优先（部署时用环境变量传密钥更安全）
        for env_name, key in (
            ("MMX_DUCKMASTER", "duck_master"),
            ("MMX_PYTHON", "python"),
            ("MMX_PROXY", "proxy"),
            ("MMX_BRIDGE_PORT", "bridge_port"),
        ):
            val = os.environ.get(env_name)
            if val:
                out[key] = val
        out["bridge_port"] = int(out.get("bridge_port") or 18791)
        out["workdir"] = str(REG_DIR)
        out["data_dir"] = str(DATA_DIR)
        out["token_file"] = str(DATA_DIR / "minimaxtoken.txt")
        return out

    def save_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        cur = self.settings()
        for key in ("python", "bridge_port", "proxy", "duck_api", "duck_master",
                    "password", "start_url", "enabled", "pool_config"):
            if key in payload:
                cur[key] = payload[key]
        cur.pop("workdir", None)
        cur.pop("data_dir", None)
        cur.pop("token_file", None)
        config.data["minimax_reg"] = cur
        config._save()
        return self.public_settings()

    def public_settings(self) -> dict[str, Any]:
        s = self.settings()
        s["duck_master_set"] = bool(s.get("duck_master"))
        s["duck_master"] = ""
        s["python_exists"] = Path(str(s.get("python") or "")).exists()
        s["script_exists"] = (REG_DIR / "auto_mmx9.py").exists()
        return s

    def _child_env(self, s: dict[str, Any], count: int = 1) -> dict[str, str]:
        env = dict(os.environ)
        env["MMX_BRIDGE_PORT"] = str(s["bridge_port"])
        env["MMX_PROXY"] = str(s["proxy"])
        env["MMX_DUCK_API"] = str(s["duck_api"])
        env["MMX_DUCKMASTER"] = str(s.get("duck_master") or "")
        env["MMX_PASSWORD"] = str(s.get("password") or "")
        env["MMX_START_URL"] = str(s.get("start_url") or "")
        env["MMX_TOKEN_FILE"] = str(DATA_DIR / "minimaxtoken.txt")
        env["MMX_TOKEN_ONLY_FILE"] = str(DATA_DIR / "minimaxtoken_only.txt")
        # ★ 看门狗必须按数量放大：注册是串行的，一个约 90~180 秒，
        #   跑 5 个接近 15 分钟；固定值会把跑到一半的账号腰斩掉。
        env["MMX_WATCHDOG_SECS"] = str(300 + 300 * max(1, int(count or 1)))
        # 出口池是共享的，别让注册脚本的看门狗把它杀掉
        env["MMX_KILL_MIHOMO"] = "0"
        # ★ 子进程 stdout 走管道时默认用系统 ANSI 编码（GBK），
        #   日志里的 ✗/★ 会 UnicodeEncodeError 把脚本整个带崩。
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        return env

    # ------------------------------------------------------------------ 任务
    def _log(self, line: str) -> None:
        line = line.rstrip()
        if not line:
            return
        with self._lock:
            logs = self._state.setdefault("log", [])
            logs.append(line)
            if len(logs) > 400:
                del logs[:-400]

    def start(self, count: int = 1) -> dict[str, Any]:
        with self._lock:
            if self._state.get("running"):
                raise MinimaxRegError("已有注册任务在跑")
        s = self.settings()
        if not s.get("duck_master"):
            raise MinimaxRegError("未配置邮箱服务主密钥（DuckMail），请先在设置里填写")
        if not Path(str(s["python"])).exists():
            raise MinimaxRegError(f"找不到注册机使用的 Python：{s['python']}")
        if not (REG_DIR / "auto_mmx9.py").exists():
            raise MinimaxRegError("缺少注册机脚本 upstreams/minimax_reg/auto_mmx9.py")
        try:
            n = max(1, min(int(count or 1), 20))
        except (TypeError, ValueError) as exc:
            raise MinimaxRegError("数量不合法") from exc

        with self._lock:
            self._state.update({
                "running": True, "total": n, "done": 0, "ok": 0,
                "log": [], "started_at": int(time.time()), "finished_at": 0, "current": "",
            })
        thread = threading.Thread(target=self._run, args=(n, s), daemon=True,
                                  name="minimax-reg")
        self._thread = thread
        thread.start()
        return {"ok": True, "total": n}

    @staticmethod
    def _bridge_alive(port: int) -> bool:
        import socket
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except Exception:
            return False

    # ------------------------------------------------------------------ 出口池
    @staticmethod
    def _tcp_open(port: int, timeout: float = 1.5) -> bool:
        import socket
        try:
            with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout):
                return True
        except Exception:
            return False

    def _pool_source(self, s: dict[str, Any]) -> list[Path]:
        """出口池源配置（支持逗号分隔的多个文件，proxies 段会合并）。"""
        cands: list[Path] = []
        raw = str(s.get("pool_config") or "").strip()
        if raw:
            cands.extend(Path(x.strip()) for x in raw.split(",") if x.strip())
        if not cands:
            cands.extend(Path(x) for x in POOL_SRC_CANDIDATES)
        out: list[Path] = []
        for p in cands:
            try:
                if p.exists() and p.is_file() and p not in out:
                    out.append(p)
            except Exception:
                continue
        return out

    @staticmethod
    def _proxy_names(txt: str) -> list[str]:
        import re
        out: list[str] = []
        in_sec = False
        for line in txt.splitlines():
            if re.match(r"^proxies:", line):
                in_sec = True
                continue
            if in_sec and re.match(r"^[A-Za-z\-]+:", line):
                break
            if not in_sec:
                continue
            # ★ 两种写法都要认：
            #   "- name: xxx"（列表项开头）
            #   "  name: xxx" （条目末尾，mihomo 也允许，residential_1031.yaml 就是这种）
            m = re.match(r'^\s*(?:-\s*)?name:\s*"?([^"\n]+)"?\s*$', line)
            if m:
                out.append(m.group(1).strip())
        return out

    @staticmethod
    def _split_proxy_entries(block: str) -> list[str]:
        """把 proxies 段拆成一个个条目（以 "- " 开头的块）。"""
        out: list[str] = []
        cur: list[str] = []
        for line in block.splitlines():
            if line.startswith("- ") and cur:
                out.append("\n".join(cur) + "\n")
                cur = [line]
            elif line.startswith("- "):
                cur = [line]
            elif cur:
                cur.append(line)
        if cur:
            out.append("\n".join(cur) + "\n")
        return out

    @staticmethod
    def _entry_name(chunk: str) -> str:
        """取一个 proxy 条目的名字（支持 "- name:" 与条目末尾 "name:" 两种写法）。"""
        import re
        m = re.search(r'^\s*(?:-\s*)?name:\s*"?([^"\n]+)"?\s*$',
                      chunk, flags=re.M)
        return m.group(1).strip() if m else ""

    @classmethod
    def _render_pool_cfg(cls, src_text: str | list[str]) -> str:
        """把源配置改造成"共享端口 + rules 分流"的出口池。

        ★ 为什么要分流：
          reCAPTCHA 的 enterprise.js 必须能加载，否则页面根本不会推进
          （window.grecaptcha 一直 undefined，表单点了没反应）。
          机场/反代节点常常连不上 google 系域名，所以把 google/recaptcha
          单独路由到 10808，minimax 域名才走池子里轮换的节点。
          —— 这正是原版 mini_flow 的设计，别改回去。

        ★ 出口轮换：
          共享端口上用 select 组 POOL，每轮注册前 PUT /proxies/POOL 切节点。
          注册是串行的，不会互相打架。
        """
        import re
        if isinstance(src_text, str):
            src_text = [src_text]
        # ① 逐条抠出每个源配置的 proxies 条目，多源合并：
        #    - 按名字去重（mihomo 不允许重名，否则启动直接 fatal）
        #    - 剔除带 dialer-proxy 的条目（它依赖源配置里的 proxy-group，
        #      我们不合并 group，带过去会 "dialer-proxy not found"）
        chunks: list[str] = []
        names: list[str] = []
        seen_names: set[str] = set()
        for one in src_text:
            m = re.search(r"^proxies:\s*$", one, flags=re.M)
            if not m:
                continue
            rest = one[m.end():]
            nxt = re.search(r"^[A-Za-z\-]+:", rest, flags=re.M)
            block = rest[:nxt.start()] if nxt else rest
            for chunk in cls._split_proxy_entries(block):
                nm = cls._entry_name(chunk)
                if not nm or nm in seen_names:
                    continue
                if "dialer-proxy" in chunk:
                    continue
                seen_names.add(nm)
                names.append(nm)
                chunks.append(chunk)
        proxies_block = "".join(chunks)
        if not names:
            return src_text[0] if src_text else ""

        head = [
            "mixed-port: %d" % POOL_MIXED_PORT,
            "allow-lan: false",
            "mode: rule",
            "log-level: warning",
            "external-controller: 127.0.0.1:%d" % POOL_CTRL_PORT,
            "ipv6: false",
            "dns:",
            "  enable: true",
            "  ipv6: false",
            "  enhanced-mode: redir-host",
            "  default-nameserver:",
            "  - 223.5.5.5",
            "  - 119.29.29.29",
            "  nameserver:",
            "  - https://dns.alidns.com/dns-query",
            "  - https://doh.pub/dns-query",
            "proxies:",
        ]
        # ② 池子出口（本机 v2rayN，专门跑 google/recaptcha）
        head.append("- name: %s" % json.dumps(V2RAY_PROXY_NAME, ensure_ascii=False))
        head.append("  type: http")
        head.append("  server: 127.0.0.1")
        head.append("  port: %d" % V2RAY_PORT)

        body = ["proxy-groups:"]
        body.append("- name: POOL")
        body.append("  type: select")
        body.append("  proxies:")
        for nm in names:
            body.append("  - %s" % json.dumps(nm, ensure_ascii=False))

        rules = ["rules:"]
        for d in MINIMAX_DOMAINS:
            rules.append("- DOMAIN-SUFFIX,%s,POOL" % d)
        # ★ google/recaptcha 默认**也跟着 POOL 走**（同一个出口 IP）：
        #   recaptcha 的 token 是绑定 IP 的，分流到别的出口它就不发 token，
        #   表单点了毫无反应（页面看着一切正常，就是不推进）。
        #   只有池子节点连不上 google 时才需要分流，代价是 token 可能被判不一致。
        if SPLIT_GOOGLE:
            for d in GOOGLE_DOMAINS:
                rules.append("- DOMAIN-SUFFIX,%s,%s" % (d, V2RAY_PROXY_NAME))
        rules.append("- MATCH,POOL")

        return "\n".join(head) + "\n" + proxies_block.rstrip() + "\n" + \
            "\n".join(body) + "\n" + "\n".join(rules) + "\n"

    @staticmethod
    def _parse_listeners(txt: str) -> list[dict[str, Any]]:
        import re
        out: list[dict[str, Any]] = []
        cur: dict[str, Any] = {}
        in_sec = False
        for line in txt.splitlines():
            if re.match(r"^listeners:", line):
                in_sec = True
                continue
            if in_sec and re.match(r"^[A-Za-z\-]+:", line):
                in_sec = False
            if not in_sec:
                continue
            m = re.match(r"^\s*- name:\s*(\S+)", line)
            if m:
                if cur:
                    out.append(cur)
                cur = {"name": m.group(1).strip('"')}
                continue
            m = re.match(r"^\s+port:\s*(\d+)", line)
            if m and cur:
                cur["port"] = int(m.group(1))
            m = re.match(r"^\s+proxy:\s*(.+)$", line)
            if m and cur:
                cur["proxy"] = m.group(1).strip().strip('"')
        if cur:
            out.append(cur)
        return [x for x in out if x.get("port")]

    @staticmethod
    def _kill_pool() -> None:
        """只杀我们自己这份池的 mihomo（按可执行文件路径匹配，别误杀别的）。"""
        import subprocess as sp
        ps = (
            "$p = Get-Process mihomo -ErrorAction SilentlyContinue | "
            "Where-Object { $_.Path -eq '%s' }; "
            "$p | ForEach-Object { Stop-Process -Id $_.Id -Force }" % MIHOMO_BIN
        )
        try:
            sp.run(["powershell", "-NoProfile", "-Command", ps],
                   capture_output=True, timeout=30)
        except Exception:
            pass
        time.sleep(1.5)

    @staticmethod
    def _switch_pool(node: str) -> bool:
        """把 POOL 组切到指定节点（共享端口，切节点是全局动作）。"""
        import urllib.request as u
        try:
            req = u.Request("http://127.0.0.1:%d/proxies/POOL" % POOL_CTRL_PORT,
                            method="PUT",
                            data=json.dumps({"name": node}).encode("utf-8"))
            u.urlopen(req, timeout=8).read()
            return True
        except Exception:
            return False

    @staticmethod
    def _probe_node(node: str, timeout: float = 5.0) -> tuple[str, str, int]:
        """切到该节点并探活，返回 (落地 IP, 国家, 首页耗时 ms)。

        ★ 必须先切 POOL 再测：共享端口上选中的节点决定出口。
        """
        import re
        import time as _t
        import urllib.request as u
        if not MinimaxRegConsole._switch_pool(node):
            return "", "", 0
        op = u.build_opener(u.ProxyHandler({
            "http": "http://127.0.0.1:%d" % POOL_MIXED_PORT,
            "https": "http://127.0.0.1:%d" % POOL_MIXED_PORT,
        }))
        ip = ""
        country = ""
        try:
            r = op.open(u.Request("http://ip-api.com/json/?fields=query,country",
                                  headers={"User-Agent": "Mozilla/5.0 Chrome/140.0.0.0"}),
                        timeout=timeout)
            body = r.read(400).decode("utf-8", "replace")
            m = re.search(r'"query"\s*:\s*"([^"]+)"', body)
            if m:
                ip = m.group(1)
            m = re.search(r'"country"\s*:\s*"([^"]+)"', body)
            if m:
                country = m.group(1)
        except Exception:
            return "", "", 0
        if not ip:
            return "", "", 0
        try:
            t0 = _t.time()
            r = op.open(u.Request("https://agent.minimax.io/",
                                  headers={"User-Agent": "Mozilla/5.0 Chrome/140.0.0.0"}),
                        timeout=timeout + 5)
            ms = int((_t.time() - t0) * 1000)
            if r.status != 200:
                return "", "", 0
        except Exception:
            return "", "", 0
        return ip, country, ms

    @staticmethod
    def _probe_local_exit(port: int, timeout: float = 12.0) -> tuple[str, str, int]:
        """测本机另一个代理工具的本地端口（Clash Verge / v2rayN 等）。"""
        import re
        import time as _t
        import urllib.request as u
        op = u.build_opener(u.ProxyHandler({
            "http": "http://127.0.0.1:%d" % port,
            "https": "http://127.0.0.1:%d" % port,
        }))
        ip = ""
        country = ""
        try:
            r = op.open(u.Request("http://ip-api.com/json/?fields=query,country",
                                  headers={"User-Agent": "Mozilla/5.0 Chrome/140.0.0.0"}),
                        timeout=timeout)
            body = r.read(400).decode("utf-8", "replace")
            m = re.search(r'"query"\s*:\s*"([^"]+)"', body)
            if m:
                ip = m.group(1)
            m = re.search(r'"country"\s*:\s*"([^"]+)"', body)
            if m:
                country = m.group(1)
        except Exception:
            return "", "", 0
        if not ip:
            return "", "", 0
        try:
            t0 = _t.time()
            r = op.open(u.Request("https://agent.minimax.io/",
                                  headers={"User-Agent": "Mozilla/5.0 Chrome/140.0.0.0"}),
                        timeout=timeout + 5)
            ms = int((_t.time() - t0) * 1000)
            if r.status != 200:
                return "", "", 0
        except Exception:
            return "", "", 0
        return ip, country, ms

    def _probe_pool(self, names: list[str], limit: int = 30) -> bool:
        """逐个切 POOL 节点探活，写 px_live.json（按落地 IP 去重，按速度排序）。

        ★ 必须串行：共享端口上切节点是全局动作，并发会互相踩。
        ★ 节点命中率很低（大量节点连不上），所以失败再重试一次。
        """
        import re
        cands = [n for n in names if n and n != V2RAY_PROXY_NAME]
        us = [n for n in cands if re.search(r"美国|US|家宽|States|高速", n)]
        rest = [n for n in cands if n not in us]
        pick = us + rest
        if len(pick) > limit:
            stride = max(1, len(pick) // limit)
            pick = pick[::stride][:limit]
        if not pick:
            return False
        self._log("探活出口 %d 个节点…" % len(pick))
        good: list[dict[str, Any]] = []
        seen: set[str] = set()
        for nm in pick:
            ip, country, ms = self._probe_node(nm)
            if not ip:
                ip, country, ms = self._probe_node(nm)      # 瞬时抖动重试一次
            if not ip or ip in seen:
                continue
            seen.add(ip)
            good.append({
                "ip": ip, "country": country, "ms": ms,
                "url": "http://127.0.0.1:%d" % POOL_MIXED_PORT,
                "name": nm, "node": nm,
            })
        # ★ 按实测速度排序（慢的会把浏览器首屏拖到超时），
        #   然后**随机打乱**：老是拿最快的几个出口，很快就会被
        #   MiniMax 按 IP 限流（同一出口注册几次后 oauth2/login 就 403）。
        good.sort(key=lambda x: x.get("ms") or 999999)

        # ★ 再把本机其它代理工具的本地端口测一遍，当额外出口。
        #   它们和机场节点不是一个 IP 段，目标站封了一批不影响另一批。
        for _port in EXTRA_EXIT_PORTS:
            _ip, _cty, _ms = self._probe_local_exit(_port)
            if not _ip:
                continue
            if _ip in seen:
                continue
            seen.add(_ip)
            good.append({
                "ip": _ip, "country": _cty, "ms": _ms,
                "url": "http://127.0.0.1:%d" % _port,
                "name": "local-%d" % _port,
                "node": "local-%d" % _port,
                "port": _port,
            })
            self._log("  · %-16s %5dms  本机代理口 %d" % (_ip, _ms, _port))

        import random as _rnd
        _rnd.shuffle(good)
        try:
            PX_LIVE.write_text(json.dumps(good, ensure_ascii=False, indent=1),
                               encoding="utf-8")
        except Exception as exc:
            self._log("出口清单写失败：%s" % exc)
            return False
        self._log("可用出口 %d 个（不同落地 IP %d 个）" % (len(good), len(seen)))
        for g in good[:6]:
            self._log("  · %-16s %5dms  %s" % (g["ip"], g.get("ms") or 0,
                                                 (g["node"] or "")[:30]))
        return bool(good)

    def _ensure_pool(self, s: dict[str, Any]) -> bool:
        """确保 mihomo 出口池在跑（mixed-port 7896 / controller 9098）。

        MiniMax 会对同一出口 IP 连续注册降级成 "Welcome to MiniMax" 形态，
        所以每轮换一个出口（端口即出口，见 _render_pool_cfg 的说明）。
        """
        import hashlib
        import re
        import shutil
        import subprocess as sp

        srcs = self._pool_source(s)
        if not srcs:
            self._log("没有出口池配置（可在设置里填 pool_config），本次用单出口")
            return False
        src_texts = []
        for src in srcs:
            try:
                src_texts.append(src.read_text(encoding="utf-8", errors="replace"))
            except Exception as exc:
                self._log("出口池配置读不出来：%s" % exc)
        if not src_texts:
            return False
        src = srcs[0]
        want = self._render_pool_cfg(src_texts)
        want_hash = hashlib.md5(want.encode("utf-8")).hexdigest()
        try:
            POOL_DIR.mkdir(parents=True, exist_ok=True)
            POOL_CFG.write_text(want, encoding="utf-8")
        except Exception as exc:
            self._log("出口池配置写失败：%s" % exc)
            return False
        geo = Path(r"D:\xi\Ai\smart-proxy\reg-exits")
        for f in ("geoip.dat", "geosite.dat"):
            sfile, dfile = geo / f, POOL_DIR / f
            if sfile.exists() and not dfile.exists():
                try:
                    shutil.copy2(sfile, dfile)
                except Exception:
                    pass
        names = [n for n in self._proxy_names(want) if n != V2RAY_PROXY_NAME]
        if not names:
            self._log("出口池配置里没有可用节点")
            return False
        try:
            PX_PORTS.write_text(json.dumps(
                [{"node": n} for n in names], ensure_ascii=False, indent=1),
                encoding="utf-8")
        except Exception:
            pass

        prev: dict[str, Any] = {}
        try:
            prev = json.loads(POOL_STATE.read_text(encoding="utf-8"))
        except Exception:
            prev = {}
        running = self._tcp_open(POOL_CTRL_PORT)
        if running and prev.get("hash") == want_hash:
            self._log("出口池已在跑（%d / %d），复用" % (POOL_MIXED_PORT, POOL_CTRL_PORT))
        else:
            if running:
                self._log("出口池配置有变化，重启")
                self._kill_pool()
            if not Path(MIHOMO_BIN).exists():
                self._log("找不到 mihomo，出口池未启动")
                return False
            try:
                out = open(POOL_DIR / "m.out.log", "ab")
                proc = sp.Popen([MIHOMO_BIN, "-d", str(POOL_DIR), "-f", str(POOL_CFG)],
                                stdout=out, stderr=out,
                                creationflags=getattr(sp, "CREATE_NO_WINDOW", 0))
                try:
                    POOL_STATE.write_text(json.dumps(
                        {"pid": proc.pid, "src": str(src), "hash": want_hash},
                        ensure_ascii=False), encoding="utf-8")
                except Exception:
                    pass
                for _ in range(30):
                    if self._tcp_open(POOL_CTRL_PORT):
                        break
                    time.sleep(1)
            except Exception as exc:
                self._log("出口池启动失败：%s" % exc)
                return False
            if not self._tcp_open(POOL_CTRL_PORT):
                self._log("出口池起来了但控制端口不通")
                return False
            self._log("出口池已启动（%d / %d）" % (POOL_MIXED_PORT, POOL_CTRL_PORT))

        # 探活结果新鲜就不重复探（auto_mmx9 每轮还会再验一次出口）
        fresh = False
        try:
            if PX_LIVE.exists() and time.time() - PX_LIVE.stat().st_mtime < 21600:
                fresh = len(json.loads(PX_LIVE.read_text(encoding="utf-8"))) >= 5
        except Exception:
            fresh = False
        if fresh:
            self._log("沿用已有出口清单")
            return True
        return self._probe_pool(names)

    def _pool_guard(self, stop_evt: threading.Event) -> None:
        """跑批期间盯着出口池，死了就拉起来。

        ★ 出口池是被多方共用的（机器上还可能有别的 mihomo），
          一旦它挂了，后续每一轮都会"连接被拒绝"，整批直接废掉。
        """
        import subprocess as sp
        while not stop_evt.is_set():
            if stop_evt.wait(15):
                return
            try:
                if self._tcp_open(POOL_CTRL_PORT) and self._tcp_open(POOL_MIXED_PORT):
                    continue
            except Exception:
                pass
            self._log("出口池掉线，正在拉起…")
            try:
                if not POOL_CFG.exists() or not Path(MIHOMO_BIN).exists():
                    continue
                out = open(POOL_DIR / "m.out.log", "ab")
                proc = sp.Popen([MIHOMO_BIN, "-d", str(POOL_DIR), "-f", str(POOL_CFG)],
                                stdout=out, stderr=out,
                                creationflags=getattr(sp, "CREATE_NO_WINDOW", 0))
                try:
                    POOL_STATE.write_text(json.dumps(
                        {"pid": proc.pid, "restarted": 1}, ensure_ascii=False),
                        encoding="utf-8")
                except Exception:
                    pass
                for _ in range(20):
                    if stop_evt.wait(1):
                        return
                    if self._tcp_open(POOL_CTRL_PORT):
                        break
                self._log("出口池已重新拉起")
            except Exception as exc:
                self._log("出口池拉起失败：%s" % exc)

    def _reset_exit_state(self) -> None:
        """新一轮开始：清掉"已用"标记，但保留"死节点"记录。

        ★ 不清的话跨轮累积，第二轮就把出口池用光了，只能回落单出口，
      然后撞上 MiniMax 对同 IP 的降级。
    """
        state_file = DATA_DIR / "px_state.json"
        try:
            st = json.loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(st, dict):
            return
        kept = {k: v for k, v in st.items() if isinstance(v, dict) and v.get("bad")}
        try:
            state_file.write_text(json.dumps(kept, ensure_ascii=False, indent=1),
                                  encoding="utf-8")
        except Exception:
            pass

    def _run(self, count: int, s: dict[str, Any]) -> None:
        env = self._child_env(s, count)
        _stop_evt: threading.Event | None = None
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._log(f"任务开始：{count} 个，python={s.get('python')}")
        try:
            self._ensure_pool(s)
            self._log("出口池就绪")
            self._reset_exit_state()
            # ★ 跑批期间盯着池，掉了自动拉起
            _stop_evt = threading.Event()
            _guard = threading.Thread(target=self._pool_guard, args=(_stop_evt,),
                                      daemon=True, name="mmx-pool-guard")
            _guard.start()
            # 端口已被占用 => 已有 bridge 在跑，直接复用。
            # 起第二个会跟它抢同一个 mails_store.jsonl，Windows 文件锁 EINVAL，
            # 表现为 POST /mail 偶发 500 OSError(22)。
            if self._bridge_alive(int(s["bridge_port"])):
                self._log(f"邮箱桥接已在跑（端口 {s['bridge_port']}），复用")
            else:
                self._log("启动邮箱桥接…")
                self._bridge = subprocess.Popen(
                    [str(s["python"]), "-u", "bridge_server.py"], cwd=str(REG_DIR), env=env,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                time.sleep(2)
                self._log(f"桥接 pid={self._bridge.pid}")
            self._log(f"开始注册 {count} 个…")
            proc = subprocess.Popen(
                [str(s["python"]), "-u", "auto_mmx9.py", str(count)],
                cwd=str(REG_DIR), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self._log(f"注册子进程 pid={proc.pid}")
            try:
                for raw in iter(proc.stdout.readline, b""):
                    line = raw.decode("utf-8", "replace").rstrip()
                    if not line:
                        continue
                    self._log(line)
                    if "★★★ 成功" in line:
                        with self._lock:
                            self._state["ok"] = int(self._state.get("ok", 0)) + 1
            except Exception as exc:
                self._log(f"读子进程输出异常：{type(exc).__name__}: {exc}")
            proc.wait(timeout=1800)
            with self._lock:
                self._state["done"] = count
        except Exception as exc:  # noqa: BLE001
            self._log(f"任务异常：{type(exc).__name__}: {exc}")
        finally:
            try:
                if _stop_evt is not None:
                    _stop_evt.set()
            except Exception:
                pass
            try:
                if self._bridge and self._bridge.poll() is None:
                    self._bridge.terminate()
                    time.sleep(1)
                    if self._bridge.poll() is None:
                        self._bridge.kill()
            except Exception:
                pass
            self._bridge = None
            with self._lock:
                self._state["running"] = False
                self._state["finished_at"] = int(time.time())
                self._state["current"] = ""

    def stop(self) -> dict[str, Any]:
        with self._lock:
            self._state["running"] = False
        try:
            if self._bridge and self._bridge.poll() is None:
                self._bridge.terminate()
        except Exception:
            pass
        return {"ok": True}

    # ------------------------------------------------------------------ 读取
    def token_file(self) -> Path:
        return DATA_DIR / "minimaxtoken.txt"

    def token_count(self) -> int:
        p = self.token_file()
        if not p.exists():
            return 0
        try:
            return sum(1 for line in p.read_text(encoding="utf-8", errors="replace").splitlines()
                       if line.strip())
        except Exception:
            return 0

    def tokens(self, limit: int = 50) -> list[dict[str, Any]]:
        p = self.token_file()
        if not p.exists():
            return []
        out: list[dict[str, Any]] = []
        try:
            lines = [x for x in p.read_text(encoding="utf-8", errors="replace").splitlines()
                     if x.strip()]
        except Exception:
            return []
        for line in reversed(lines[-max(1, min(limit, 500)):]):
            parts = line.split("----")
            if len(parts) < 3:
                continue
            tok = parts[2]
            out.append({
                "email": parts[0],
                "created_at": parts[1],
                "token_masked": tok[:24] + ("…" if len(tok) > 24 else ""),
                "token_len": len(tok),
            })
        return out

    def _pool_info(self) -> dict[str, Any]:
        """出口池概况（界面只展示功能状态，不暴露实现细节）。"""
        info: dict[str, Any] = {"ready": False, "ports": 0, "exits": 0, "distinct_ips": 0}
        try:
            info["ready"] = self._tcp_open(POOL_CTRL_PORT)
        except Exception:
            pass
        try:
            ports = json.loads(PX_PORTS.read_text(encoding="utf-8"))
            info["ports"] = len(ports) if isinstance(ports, list) else 0
        except Exception:
            pass
        try:
            live = json.loads(PX_LIVE.read_text(encoding="utf-8"))
            if isinstance(live, list):
                info["exits"] = len(live)
                info["distinct_ips"] = len({x.get("ip") for x in live if isinstance(x, dict) and x.get("ip")})
        except Exception:
            pass
        return info

    def status(self) -> dict[str, Any]:
        with self._lock:
            state = dict(self._state)
        state["log"] = state.get("log", [])[-120:]
        state["token_count"] = self.token_count()
        state["settings"] = self.public_settings()
        state["recent_tokens"] = self.tokens(10)
        state["exit_pool"] = self._pool_info()
        return state


minimax_reg_console = MinimaxRegConsole()
