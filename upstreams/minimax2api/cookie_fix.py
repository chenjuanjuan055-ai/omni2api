"""minimax_cookie_fix.py — a tiny reverse proxy that rewrites auth.

The panel (minimax2api) calls upstream with `Authorization: Bearer <jwt>`,
but MiniMax only accepts `Cookie: _token=<jwt>`. This proxy sits in front of
agent.minimax.io, rewrites the header, and passes everything else through.

Point the panel's `settings.upstream.baseURL` at this and done.

Run:
  python minimax_cookie_fix.py [PORT]        # default 18100
"""
import http.server, json, socket, ssl, sys, threading, time, urllib.request, urllib.error

UPSTREAM = "https://agent.minimax.io"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 18100

_sctx = ssl.create_default_context()
_sctx.check_hostname = False
_sctx.verify_mode = ssl.CERT_NONE

# headers the browser sends that we mirror so upstream sees a sane client
BASE_HDR = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Origin": "https://agent.minimax.io",
    "Referer": "https://agent.minimax.io/",
}

HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
       "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length"}


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


class Proxy(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _extract_token(self, hdrs):
        """Pull the JWT out of whatever auth shape the panel used."""
        auth = hdrs.get("Authorization") or hdrs.get("authorization") or ""
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        ck = hdrs.get("Cookie") or hdrs.get("cookie") or ""
        for part in ck.split(";"):
            part = part.strip()
            if part.startswith("_token="):
                return part[len("_token="):]
        # some builds put it in x-api-key
        for k in ("x-api-key", "X-Api-Key", "token", "x-token"):
            if hdrs.get(k):
                return hdrs[k].strip()
        return None

    def _forward(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(n) if n else None
        tok = self._extract_token(self.headers)

        out = dict(BASE_HDR)
        for k, v in self.headers.items():
            if k.lower() not in HOP and k.lower() not in ("cookie", "authorization"):
                out[k] = v
        if tok:
            # send BOTH shapes: older /v1/* endpoints want Cookie,
            # /minimax-cloud/api/v1/* wants the Authorization bearer.
            out["Cookie"] = f"_token={tok}"
            out.setdefault("Authorization", f"Bearer {tok}")
            out.setdefault("authorization", f"Bearer {tok}")

        # debug: log the sign-related headers we received and will send
        dbg = {k: out[k] for k in out
               if k.lower() in ("x-signature", "x-timestamp", "cookie", "yy")}
        dbg_safe = {k: (str(v)[:40] if k.lower() != "cookie" else "set") for k, v in dbg.items()}
        log(f"  hdrs: {dbg_safe}")

        url = UPSTREAM + self.path
        req = urllib.request.Request(url, data=body, method=self.command, headers=out)
        try:
            with urllib.request.urlopen(req, timeout=900, context=_sctx) as resp:
                data = resp.read()
                self.send_response(resp.status)
                for k, v in resp.headers.items():
                    if k.lower() not in HOP:
                        self.send_header(k, v)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                log(f"{self.command} {self.path.split('?')[0]} -> {resp.status} "
                    f"(token={'yes' if tok else 'NO'}) {len(data)}B")
        except urllib.error.HTTPError as e:
            data = e.read()
            self.send_response(e.code)
            self.send_header("Content-Type", e.headers.get("Content-Type", "application/json"))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            log(f"{self.command} {self.path.split('?')[0]} -> {e.code} "
                f"(token={'yes' if tok else 'NO'}) {data[:120]}")
        except Exception as e:
            msg = json.dumps({"error": {"message": f"proxy error: {repr(e)[:120]}"}}).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)
            log(f"{self.command} {self.path.split('?')[0]} -> 502 {repr(e)[:80]}")

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _forward


if __name__ == "__main__":
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Proxy)
    srv.daemon_threads = True
    log(f"minimax cookie-fix proxy on http://127.0.0.1:{PORT} -> {UPSTREAM}")
    srv.serve_forever()
