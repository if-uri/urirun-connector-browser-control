# Real browser control via Chrome DevTools Protocol — no xdotool/ydotool, works headed
# under Wayland. Launch Chrome with a debug port; navigate/eval/screenshot over CDP.
# Plain fn(**payload), deployable with `urirun host deploy --code cdp-flat-handler.py`.
import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import time
import urllib.parse
import urllib.request

PORT = int(os.environ.get("CDP_PORT", "9222"))
_CHROME = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome", "brave-browser", "microsoft-edge")


def _bin(name=""):
    for c in ((name,) if name else _CHROME):
        if shutil.which(c):
            return shutil.which(c)
    return None


def _http(path, method="GET"):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", method=method)
    return json.loads(urllib.request.urlopen(req, timeout=5).read() or "{}")


def _pages():
    return [t for t in _http("/json") if t.get("type") == "page"]


# --- minimal WebSocket client (stdlib only) for CDP commands ---
def _ws(ws_url, messages):
    u = urllib.parse.urlparse(ws_url)
    s = socket.create_connection((u.hostname, u.port), timeout=6)
    key = base64.b64encode(os.urandom(16)).decode()
    s.sendall((f"GET {u.path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        buf += s.recv(4096)
    out = []
    for msg in messages:
        _send(s, json.dumps(msg))
        while True:
            data = _recv(s)
            if data is None:
                break
            obj = json.loads(data)
            if obj.get("id") == msg["id"]:
                out.append(obj)
                break
    s.close()
    return out


def _send(s, text):
    p = text.encode()
    mask = os.urandom(4)
    h = bytearray([0x81])
    n = len(p)
    if n < 126:
        h.append(0x80 | n)
    elif n < 65536:
        h.append(0x80 | 126); h += struct.pack(">H", n)
    else:
        h.append(0x80 | 127); h += struct.pack(">Q", n)
    h += mask
    s.sendall(bytes(h) + bytes(b ^ mask[i % 4] for i, b in enumerate(p)))


def _recv(s):
    def rd(n):
        b = b""
        while len(b) < n:
            c = s.recv(n - len(b))
            if not c:
                return None
            b += c
        return b
    h = rd(2)
    if not h:
        return None
    ln = h[1] & 0x7f
    if ln == 126:
        ln = struct.unpack(">H", rd(2))[0]
    elif ln == 127:
        ln = struct.unpack(">Q", rd(8))[0]
    return (rd(ln) or b"").decode("utf-8", "replace")


def _cmd(method, params=None):
    pages = _pages()
    if not pages:
        return {"ok": False, "error": "no page target (launch first)"}
    res = _ws(pages[0]["webSocketDebuggerUrl"], [{"id": 1, "method": method, "params": params or {}}])
    return res[0] if res else {}


# --- routes ---
def launch(**p):
    browser, url, headless = p.get("browser", "chrome"), p.get("url", "about:blank"), p.get("headless", False)
    b = _bin(browser if browser != "chrome" else "")
    if not b:
        return {"ok": False, "error": f"no Chrome-family browser for {browser!r}"}
    args = [b, f"--remote-debugging-port={PORT}", "--remote-debugging-address=127.0.0.1",
            "--user-data-dir=/tmp/urirun-cdp-profile", "--no-first-run", "--no-default-browser-check"]
    if headless:
        args.append("--headless=new")
    args.append(url)
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(40):
        try:
            ver = _http("/json/version")
            return {"ok": True, "pid": proc.pid, "debugPort": PORT, "browser": ver.get("Browser"), "url": url}
        except Exception:
            time.sleep(0.25)
    return {"ok": False, "error": "debugger did not come up", "pid": proc.pid}


def tabs(**p):
    return {"ok": True, "tabs": [{"id": t["id"], "title": t.get("title"), "url": t.get("url")} for t in _pages()]}


def nav(**p):
    url = p.get("url", "")
    try:
        r = _http(f"/json/new?{urllib.parse.quote(url, safe='')}", method="PUT")
        return {"ok": True, "via": "http", "id": r.get("id"), "url": r.get("url")}
    except Exception:
        r = _cmd("Page.navigate", {"url": url})       # fallback: navigate current tab over WS
        return {"ok": "error" not in r, "via": "ws", "result": r.get("result")}


def eval_js(**p):
    r = _cmd("Runtime.evaluate", {"expression": p.get("expr", ""), "returnByValue": True, "awaitPromise": True})
    res = (r.get("result") or {})
    if res.get("exceptionDetails"):
        return {"ok": False, "error": res["exceptionDetails"].get("text"), "detail": str(res["exceptionDetails"])[:200]}
    val = (res.get("result") or {})
    return {"ok": True, "value": val.get("value"), "type": val.get("type")}


def screenshot(**p):
    r = _cmd("Page.captureScreenshot", {"format": "png"})
    data = (r.get("result") or {}).get("data")
    if not data:
        return {"ok": False, "error": "no screenshot data", "raw": str(r)[:200]}
    return {"ok": True, "mime": "image/png", "bytes": len(base64.b64decode(data)), "base64_head": data[:60]}
