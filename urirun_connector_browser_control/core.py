# Author: Tom Sapletta · https://tom.sapletta.com
# Part of the ifURI solution.
#
# browser-control connector — v2 authoring. Each route is declared ONCE as a
# ``@handler`` (the typed signature is the input schema, the body is the work).
# Routes are ``isolated=True`` → the ``local-function-subprocess`` adapter runs them
# out-of-process through the shared ``python -m urirun.exec`` runner: registry-portable
# (executes from a compiled/served registry with only the package importable) AND
# crash-isolated (rendering an untrusted page / driving Chrome can't take the host
# down). No argv ``*_command`` twin, no ``run_route`` dispatch, no ``_exec.py`` shim,
# no hand-written ``main``/``manifest``/``bindings`` — all derived from the handlers.

from __future__ import annotations

import base64
import importlib
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any

import urirun

CONNECTOR_ID = "browser-control"
CONNECTOR = urirun.connector(CONNECTOR_ID, scheme="browser", target="desktop", meta={"label": "Browser Control"})
# Local headless Chrome routes (browser://chrome/...) live in the same connector.
CHROME = urirun.connector(CONNECTOR_ID, scheme="browser", target="chrome", meta={"label": "Chrome (headless)"})
# KVM target (browser://kvm/...): drive ANY visible browser (firefox/chrome/…) by GUI —
# launch + keyboard/mouse/screenshot/OCR-click — implemented by reusing the tellmesh
# modules (urihim, urikvm, uriscreen) as the URI handlers. Real screen control, not
# headless, so it works with every browser, its extensions, logins and plugins.
KVM = urirun.connector(CONNECTOR_ID, scheme="browser", target="kvm", meta={"label": "Browser via KVM (any browser)"})


_BROWSERS = {
    "firefox": ("firefox", "firefox-esr"),
    "chrome": ("google-chrome", "google-chrome-stable", "chrome"),
    "chromium": ("chromium", "chromium-browser"),
    "brave": ("brave-browser", "brave"),
    "edge": ("microsoft-edge", "microsoft-edge-stable", "msedge"),
    "opera": ("opera",),
    "vivaldi": ("vivaldi", "vivaldi-stable"),
}


def _browser_bin(browser: str) -> str | None:
    """Resolve a browser name to an executable; an unknown name is tried as a binary."""
    for cand in _BROWSERS.get(browser, (browser,)):
        path = shutil.which(cand)
        if path:
            return path
    return None


def _has_display() -> bool:
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


# --- tellmesh integration: reuse ../tellmesh/* handlers as the URI implementation ---

_TM_CTX: dict[str, Any] = {"state": {}, "config": {},
                           "allow_real": os.environ.get("URISYS_ALLOW_REAL") == "1"}


def _ensure_tellmesh_path() -> None:
    """Put tellmesh pack sources + uri_control on sys.path from $TELLMESH_DIR (so the
    handlers import straight from a checkout when not pip-installed)."""
    import sys
    tm = os.environ.get("TELLMESH_DIR")
    if not tm:
        return
    base = Path(tm)
    for rel in ("uricontrol/core/python", "urihim", "urikvm", "uriscreen", "uribrowser", "urioffice", "urishell"):
        p = base / rel
        if p.is_dir() and str(p) not in sys.path:
            sys.path.insert(0, str(p))


def _tm(module: str, func: str, payload: dict) -> dict | None:
    """Call a tellmesh handler ``fn(payload, context)`` by ``module:func`` (reusing a
    persistent context). Returns ``None`` when the module isn't importable so the caller
    can fall back to the bare OS tools (xdotool/ydotool/grim/…)."""
    try:
        fn = getattr(importlib.import_module(module), func)
    except Exception:
        _ensure_tellmesh_path()
        try:
            fn = getattr(importlib.import_module(module), func)
        except Exception:
            return None
    try:
        out = fn(payload, _TM_CTX)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{module}.{func} failed: {exc}"}
    return out if isinstance(out, dict) else {"ok": True, "result": out}


# --- bare OS-tool layer: the same tools tellmesh wraps, so KVM control works even on a
# node WITHOUT the tellmesh packs. Wayland-first (ydotool/grim) then X11 (xdotool/import).

def _wayland() -> bool:
    return bool(os.environ.get("WAYLAND_DISPLAY")) and not os.environ.get("DISPLAY")


def _input_tool() -> str | None:
    """ydotool drives both Wayland and X11; xdotool is X11-only."""
    if shutil.which("ydotool"):
        return "ydotool"
    if shutil.which("xdotool") and not _wayland():
        return "xdotool"
    return None


def _run(cmd: list[str], timeout: float = 10.0) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _os_type(text: str) -> dict[str, Any]:
    tool = _input_tool()
    if not tool:
        return {"ok": False, "error": "no input tool (install ydotool for Wayland, or xdotool for X11)"}
    r = _run([tool, "type", text])
    return {"ok": r.returncode == 0, "via": tool, "typed": text}


def _os_key(combo: str) -> dict[str, Any]:
    tool = _input_tool()
    if not tool:
        return {"ok": False, "error": "no input tool (install ydotool/xdotool)"}
    r = _run([tool, "key", combo])
    return {"ok": r.returncode == 0, "via": tool, "keys": combo}


def _os_click(x: int, y: int, button: str) -> dict[str, Any]:
    if shutil.which("xdotool") and not _wayland():
        r = _run(["xdotool", "mousemove", str(x), str(y), "click",
                  {"left": "1", "middle": "2", "right": "3"}.get(button, "1")])
        return {"ok": r.returncode == 0, "via": "xdotool", "x": x, "y": y, "button": button}
    return {"ok": False, "error": "coordinate click needs xdotool (X11); on Wayland use page/command/click-text"}


def _os_screenshot() -> dict[str, Any]:
    import base64
    path = f"/tmp/urirun-browser-shot-{os.getpid()}.png"
    # X11-native / Wayland-native tools first (no session DBus, won't hang in a service);
    # gnome-screenshot/spectacle need a portal+session bus, so try them last.
    for cmd in (["grim", path], ["import", "-window", "root", path], ["scrot", "-o", path],
                ["maim", path], ["gnome-screenshot", "-f", path], ["spectacle", "-b", "-n", "-o", path]):
        if not shutil.which(cmd[0]):
            continue
        try:
            _run(cmd, timeout=8)
        except Exception:  # noqa: BLE001 - a tool hung/failed; try the next
            continue
        if os.path.exists(path):
            data = Path(path).read_bytes()
            return {"ok": True, "via": cmd[0], "path": path, "bytes": len(data),
                    "base64_head": base64.b64encode(data).decode()[:60]}
    return {"ok": False, "error": "no working screenshot tool (grim/import/scrot/maim/gnome-screenshot)"}


# --- remote-forward transport (real logic) --------------------------------

def _target_endpoint(target: str) -> str | None:
    endpoint = os.getenv("BROWSER_CONTROL_ENDPOINT")
    if endpoint:
        return endpoint.rstrip("/")
    mapping = os.getenv("URI_SERVICE_MAP")
    if not mapping:
        return None
    try:
        table = json.loads(mapping)
    except json.JSONDecodeError:
        return None
    value = table.get(target)
    return str(value).rstrip("/") if value else None


def _post_run(endpoint: str, uri: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    body = json.dumps({"uri": uri, "payload": payload}).encode("utf-8")
    request = urllib.request.Request(f"{endpoint}/run", data=body, headers={"Content-Type": "application/json"}, method="POST")
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8") or "{}")
            status = int(response.status)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8") if exc.fp else ""
        data = json.loads(raw or "{}")
        status = int(exc.code)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    return {"ok": bool(data.get("ok", status < 400)), "forwarded": True, "endpoint": endpoint,
            "status": status, "elapsedMs": elapsed_ms, "response": data, "result": data.get("result")}


# --- local headless Chrome helpers ----------------------------------------

def _chrome_bin() -> str | None:
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
        path = shutil.which(name)
        if path:
            return path
    return None


def _chrome_run(args: list[str], timeout: float = 30.0):
    chrome = _chrome_bin()
    if not chrome:
        return None, {"ok": True, "connector": CONNECTOR_ID, "target": "chrome", "executed": False,
                      "backend": "none", "reason": "no Chrome/Chromium binary found; install one to run headless routes"}
    return subprocess.run([chrome, "--headless=new", "--disable-gpu", *args], capture_output=True, text=True, timeout=timeout), None


# --- routes: one typed @handler each, run out-of-process (isolated) --------

@CONNECTOR.handler("page/command/open", isolated=True, meta={"label": "Open browser page"})
def open_page(url: str, target: str = "desktop", timeout: float = 10.0) -> dict[str, Any]:
    """Open a page on a forwarded browser node, or the local host browser."""
    endpoint = _target_endpoint(target)
    if endpoint:
        result = _post_run(endpoint, f"browser://{target}/page/command/open", {"url": url}, timeout)
        result.update({"connector": CONNECTOR_ID, "target": target, "url": url})
        return result
    if os.getenv("BROWSER_CONTROL_ALLOW_LOCAL") == "1":
        opened = webbrowser.open(url)
        return {"ok": bool(opened), "connector": CONNECTOR_ID, "target": target, "url": url,
                "executed": bool(opened), "backend": "local-webbrowser"}
    return {"ok": True, "connector": CONNECTOR_ID, "target": target, "url": url, "executed": False, "backend": "none",
            "reason": "Set BROWSER_CONTROL_ENDPOINT or URI_SERVICE_MAP to forward to a noVNC/urirun node. Set BROWSER_CONTROL_ALLOW_LOCAL=1 to open the local host browser."}


@CONNECTOR.handler("page/command/screenshot", isolated=True, meta={"label": "Capture browser screenshot"})
def capture_screenshot(url: str, target: str = "desktop", output: str = "browser-screenshot.png", timeout: float = 10.0) -> dict[str, Any]:
    """Capture a screenshot on a forwarded browser node."""
    endpoint = _target_endpoint(target)
    if endpoint:
        result = _post_run(endpoint, f"browser://{target}/page/command/screenshot", {"url": url, "output": output}, timeout)
        result.update({"connector": CONNECTOR_ID, "target": target, "url": url, "output": output})
        return result
    return {"ok": True, "connector": CONNECTOR_ID, "target": target, "url": url, "output": output, "executed": False,
            "backend": "none", "reason": "Set BROWSER_CONTROL_ENDPOINT or URI_SERVICE_MAP to forward screenshots to a browser/noVNC node."}


@CHROME.handler("page/query/dom", isolated=True, meta={"label": "Read page DOM via headless Chrome", "cliAlias": "chrome-dom"})
def chrome_dom(url: str = "", max: int = 4000) -> dict[str, Any]:
    if not url:
        return {"ok": False, "connector": CONNECTOR_ID, "target": "chrome", "error": "url is required"}
    proc, fallback = _chrome_run(["--dump-dom", url])
    if fallback:
        return {**fallback, "url": url}
    html = proc.stdout or ""
    return {"ok": proc.returncode == 0, "connector": CONNECTOR_ID, "target": "chrome", "url": url,
            "bytes": len(html), "html": html[: int(max)]}


@CHROME.handler("page/query/text", isolated=True, meta={"label": "Read page text via headless Chrome", "cliAlias": "chrome-text"})
def chrome_text(url: str = "", max: int = 2000) -> dict[str, Any]:
    result = chrome_dom(url, max=200000)
    if not result.get("ok") or "html" not in result:
        return result
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", result["html"], flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = " ".join(text.split())
    return {"ok": True, "connector": CONNECTOR_ID, "target": "chrome", "url": url, "text": text[: int(max)]}


@CHROME.handler("page/command/screenshot", isolated=True, meta={"label": "Screenshot via headless Chrome", "cliAlias": "chrome-screenshot"})
def chrome_screenshot(url: str = "", output: str = "chrome-screenshot.png") -> dict[str, Any]:
    if not url:
        return {"ok": False, "connector": CONNECTOR_ID, "target": "chrome", "error": "url is required"}
    proc, fallback = _chrome_run([f"--screenshot={output}", "--window-size=1280,800", url])
    if fallback:
        return {**fallback, "url": url, "output": output}
    saved = os.path.exists(output)
    return {"ok": proc.returncode == 0 and saved, "connector": CONNECTOR_ID, "target": "chrome",
            "url": url, "output": output, "saved": saved}


# --- KVM routes: drive ANY browser by GUI ----------------------------------
# launch uses the connector's browser-agnostic lookup; keyboard/mouse/screen/OCR prefer
# the tellmesh urihim/urikvm handlers (which abstract X11 *and* Wayland), and fall back to
# the bare OS tools (ydotool/xdotool, grim/import) when tellmesh isn't on the node — so the
# SAME control surface works for firefox, chrome, brave, edge, … with or without tellmesh.

def _tag(d: dict, **extra) -> dict[str, Any]:
    return {"connector": CONNECTOR_ID, "target": "kvm", **extra, **d}


@KVM.handler("session/command/launch", isolated=True, meta={"label": "Launch any browser (firefox/chrome/…)"})
def launch(browser: str = "firefox", url: str = "") -> dict[str, Any]:
    """Launch a visible browser window (browser-agnostic) on the node's desktop."""
    binpath = _browser_bin(browser)
    if not binpath:
        return _tag({"ok": False, "error": f"no binary for browser {browser!r}",
                     "tried": list(_BROWSERS.get(browser, (browser,)))}, browser=browser)
    if not _has_display():
        return _tag({"ok": False, "binary": binpath,
                     "error": "no DISPLAY/WAYLAND_DISPLAY — a GUI browser needs a desktop session"}, browser=browser)
    proc = subprocess.Popen([binpath, *([url] if url else [])])
    return _tag({"ok": True, "binary": binpath, "pid": proc.pid, "url": url}, browser=browser)


@KVM.handler("input/command/type", isolated=True, meta={"label": "Type text into the focused field"})
def type_text(text: str, enter: bool = False) -> dict[str, Any]:
    res = _tm("urihim.handlers", "keyboard_type", {"text": text})
    via = "tellmesh"
    if res is None:                                   # no tellmesh → bare OS tool
        res, via = _os_type(text), None
    if enter and res.get("ok"):
        res["enter"] = (_tm("urihim.handlers", "keyboard_key", {"key": "enter"}) or _os_key("Return"))
    return _tag(res, **({"via": via} if via else {}))


@KVM.handler("input/command/hotkey", isolated=True, meta={"label": "Press a keyboard shortcut"})
def hotkey(keys: list[str]) -> dict[str, Any]:
    res = _tm("urihim.handlers", "keyboard_hotkey", {"keys": keys}) or _os_key("+".join(keys))
    return _tag(res)


@KVM.handler("page/command/navigate", isolated=True, meta={"label": "Navigate (focus address bar, type URL, Enter)"})
def navigate(url: str, enter: bool = True) -> dict[str, Any]:
    """Browser-agnostic navigate via the keyboard: Ctrl+L → type URL → Enter."""
    steps = {"focus": hotkey(["ctrl", "l"]), "type": type_text(url)}
    if enter:
        steps["enter"] = (_tm("urihim.handlers", "keyboard_key", {"key": "enter"}) or _os_key("Return"))
    return _tag({"ok": all(s.get("ok", False) for s in steps.values()), "url": url, "steps": steps})


@KVM.handler("input/command/click", isolated=True, meta={"label": "Click at screen coordinates"})
def click(x: int, y: int, button: str = "left") -> dict[str, Any]:
    res = _tm("urihim.handlers", "mouse_click", {"x": x, "y": y, "button": button}) or _os_click(x, y, button)
    return _tag(res)


@KVM.handler("page/command/click-text", isolated=True, meta={"label": "Click an on-screen label (OCR)"})
def click_text(text: str) -> dict[str, Any]:
    """OCR-locate a visible label and click it — works on any browser's chrome/content."""
    res = _tm("urikvm.handlers", "click_text", {"text": text})
    if res is None:
        res = {"ok": False, "error": "click-text needs tellmesh urikvm (OCR) or tesseract on the node"}
    return _tag(res)


@KVM.handler("screen/query/capture", isolated=True, meta={"label": "Capture the screen (any browser visible)"})
def capture(monitor: int = 0) -> dict[str, Any]:
    res = _tm("urikvm.handlers", "screenshot", {"monitor": monitor}) or _os_screenshot()
    return _tag(res)


@KVM.handler("session/command/close", isolated=True, meta={"label": "Close the active browser tab/window"})
def close(hard: bool = False, browser: str = "") -> dict[str, Any]:
    """Close the active tab (Ctrl+W), or kill the browser process if hard."""
    if hard and browser:
        binpath = _browser_bin(browser)
        name = Path(binpath).name if binpath else browser
        subprocess.run(["pkill", "-f", name], capture_output=True)
        return _tag({"ok": True, "killed": name})
    res = _tm("urihim.handlers", "keyboard_hotkey", {"keys": ["ctrl", "w"]}) or _os_key("ctrl+w")
    return _tag(res)


# --- CDP target: real Chrome-family control without OS input tools -----------
# browser://cdp/... drives Chrome/Chromium/Brave/Edge via the DevTools Protocol — launch
# with a debug port, then navigate / run JS / screenshot. Needs NO xdotool/ydotool and
# works headed under Wayland (where synthetic input is otherwise blocked). Chrome-family
# only (Firefox has its own protocol); for browser-agnostic GUI control use browser://kvm.
CDP = urirun.connector(CONNECTOR_ID, scheme="browser", target="cdp", meta={"label": "Chrome via DevTools Protocol"})
_CDP_PORT = int(os.environ.get("CDP_PORT", "9222"))
_CDP_CHROME = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome", "brave-browser", "microsoft-edge")


def _cdp_http(path: str, method: str = "GET") -> Any:
    req = urllib.request.Request(f"http://127.0.0.1:{_CDP_PORT}{path}", method=method)
    return json.loads(urllib.request.urlopen(req, timeout=5).read() or "{}")


def _cdp_pages() -> list[dict]:
    return [t for t in _cdp_http("/json") if t.get("type") == "page"]


def _cdp_ws(ws_url: str, messages: list[dict]) -> list[dict]:
    import socket
    import struct
    u = urllib.parse.urlparse(ws_url)
    s = socket.create_connection((u.hostname, u.port), timeout=6)
    s.sendall((f"GET {u.path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: {base64.b64encode(os.urandom(16)).decode()}\r\n"
               f"Sec-WebSocket-Version: 13\r\n\r\n").encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        buf += s.recv(4096)

    def send(text: str) -> None:
        p = text.encode()
        mask, h, n = os.urandom(4), bytearray([0x81]), len(p)
        if n < 126:
            h.append(0x80 | n)
        elif n < 65536:
            h.append(0x80 | 126); h += struct.pack(">H", n)
        else:
            h.append(0x80 | 127); h += struct.pack(">Q", n)
        h += mask
        s.sendall(bytes(h) + bytes(b ^ mask[i % 4] for i, b in enumerate(p)))

    def rd(n: int) -> bytes | None:
        b = b""
        while len(b) < n:
            c = s.recv(n - len(b))
            if not c:
                return None
            b += c
        return b

    def recv() -> str | None:
        h = rd(2)
        if not h:
            return None
        ln = h[1] & 0x7f
        if ln == 126:
            ln = struct.unpack(">H", rd(2))[0]
        elif ln == 127:
            ln = struct.unpack(">Q", rd(8))[0]
        return (rd(ln) or b"").decode("utf-8", "replace")

    out = []
    for msg in messages:
        send(json.dumps(msg))
        while True:
            data = recv()
            if data is None:
                break
            obj = json.loads(data)
            if obj.get("id") == msg["id"]:
                out.append(obj)
                break
    s.close()
    return out


def _cdp_cmd(method: str, params: dict | None = None) -> dict:
    pages = _cdp_pages()
    if not pages:
        return {"ok": False, "error": "no page target (launch first)"}
    res = _cdp_ws(pages[0]["webSocketDebuggerUrl"], [{"id": 1, "method": method, "params": params or {}}])
    return res[0] if res else {}


@CDP.handler("session/command/launch", isolated=True, meta={"label": "Launch Chrome-family with a debug port"})
def cdp_launch(browser: str = "chrome", url: str = "about:blank", headless: bool = False) -> dict[str, Any]:
    binpath = next((shutil.which(c) for c in ((browser,) if browser != "chrome" else _CDP_CHROME) if shutil.which(c)), None)
    if not binpath:
        return {"ok": False, "connector": CONNECTOR_ID, "target": "cdp", "error": f"no Chrome-family browser for {browser!r}"}
    args = [binpath, f"--remote-debugging-port={_CDP_PORT}", "--remote-debugging-address=127.0.0.1",
            "--user-data-dir=/tmp/urirun-cdp-profile", "--no-first-run", "--no-default-browser-check"]
    if headless:
        args.append("--headless=new")
    proc = subprocess.Popen([*args, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(40):
        try:
            ver = _cdp_http("/json/version")
            return {"ok": True, "connector": CONNECTOR_ID, "target": "cdp", "pid": proc.pid,
                    "debugPort": _CDP_PORT, "browser": ver.get("Browser"), "url": url}
        except Exception:  # noqa: BLE001 - debugger not up yet
            time.sleep(0.25)
    return {"ok": False, "connector": CONNECTOR_ID, "target": "cdp", "error": "debugger did not come up", "pid": proc.pid}


@CDP.handler("page/query/tabs", isolated=True, meta={"label": "List open tabs"})
def cdp_tabs() -> dict[str, Any]:
    return {"ok": True, "connector": CONNECTOR_ID, "target": "cdp",
            "tabs": [{"id": t["id"], "title": t.get("title"), "url": t.get("url")} for t in _cdp_pages()]}


@CDP.handler("page/command/navigate", isolated=True, meta={"label": "Open/navigate a tab"})
def cdp_navigate(url: str) -> dict[str, Any]:
    try:
        r = _cdp_http(f"/json/new?{urllib.parse.quote(url, safe='')}", method="PUT")
        return {"ok": True, "connector": CONNECTOR_ID, "target": "cdp", "via": "http", "id": r.get("id"), "url": r.get("url")}
    except Exception:  # noqa: BLE001 - /json/new disabled → navigate current tab over WS
        r = _cdp_cmd("Page.navigate", {"url": url})
        return {"ok": "error" not in r, "connector": CONNECTOR_ID, "target": "cdp", "via": "ws", "result": r.get("result")}


@CDP.handler("page/query/eval", isolated=True, meta={"label": "Run JS in the page (click/fill/read)"})
def cdp_eval(expr: str) -> dict[str, Any]:
    r = _cdp_cmd("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True})
    res = r.get("result") or {}
    if "error" in r:
        return {"ok": False, "connector": CONNECTOR_ID, "target": "cdp", "error": r.get("error")}
    if res.get("exceptionDetails"):
        return {"ok": False, "connector": CONNECTOR_ID, "target": "cdp", "error": res["exceptionDetails"].get("text")}
    val = res.get("result") or {}
    return {"ok": True, "connector": CONNECTOR_ID, "target": "cdp", "value": val.get("value"), "type": val.get("type")}


@CDP.handler("page/query/screenshot", isolated=True, meta={"label": "Screenshot the live page (CDP)"})
def cdp_screenshot() -> dict[str, Any]:
    r = _cdp_cmd("Page.captureScreenshot", {"format": "png"})
    data = (r.get("result") or {}).get("data")
    if not data:
        return {"ok": False, "connector": CONNECTOR_ID, "target": "cdp", "error": "no screenshot data"}
    return {"ok": True, "connector": CONNECTOR_ID, "target": "cdp", "mime": "image/png",
            "bytes": len(base64.b64decode(data)), "base64_head": data[:60]}


# authoring surface — all derived from the declared @handlers, zero boilerplate.
urirun_bindings = CONNECTOR.bindings


def connector_manifest() -> dict[str, Any]:
    """Full manifest: prose from connector.manifest.json + machine fields derived
    from the @handlers (routes/uriSchemes/adapterKinds), so they can't drift."""
    return CONNECTOR.manifest(urirun.load_manifest(__package__))


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: subcommands + dispatch + manifest, all derived from the handlers."""
    return CONNECTOR.cli(argv, manifest_prose=urirun.load_manifest(__package__))


if __name__ == "__main__":
    import sys

    raise SystemExit(main())
